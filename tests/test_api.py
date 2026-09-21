from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.backends.base import RegisterResult
from app.config import Settings
from app.main import create_app


def make_settings(tmp_path: Path, **overrides) -> Settings:
    payload = dict(
        host="127.0.0.1",
        port=8091,
        data_dir=tmp_path,
        database_path=tmp_path / "pool.sqlite",
        secrets_path=tmp_path / "secrets.json",
        exports_dir=tmp_path / "exports",
        profiles_dir=tmp_path / "profiles",
        cliproxy_user="userbase",
        cliproxy_pass="secret",
        cliproxy_host="us.arxlabs.io",
        cliproxy_port=3010,
        default_region="US",
        sticky_minutes=30,
        queue_workers=1,
        default_backend="automation",
        sid_prefix="ts",
    )
    payload.update(overrides)
    return Settings(**payload)

MAILBOX_LINE = "alice@outlook.com----unused----client-a----refresh-a\n"


async def _import(client, text: str = MAILBOX_LINE):
    response = await client.post("/v1/mailboxes/import", json={"text": text})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    return body["data"]


@pytest.mark.asyncio
async def test_health_and_extra_forbid(client):
    http, _app = client
    health = await http.get("/health")
    assert health.status_code == 200
    assert health.json() == {"ok": True, "data": {"status": "ok"}, "error": None}

    bad = await http.post("/v1/mailboxes/import", json={"text": MAILBOX_LINE, "oops": 1})
    assert bad.status_code == 422
    payload = bad.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_import_list_and_exact_one_of(client):
    http, _app = client
    both = await http.post("/v1/mailboxes/import", json={"path": "x", "text": MAILBOX_LINE})
    assert both.status_code == 400
    assert both.json()["error"]["code"] == "mailbox_invalid"

    data = await _import(http)
    assert data["count"] == 1
    listed = await http.get("/v1/mailboxes")
    assert listed.json()["data"]["mailboxes"][0]["email"] == "alice@outlook.com"
    assert "refresh_token" not in listed.json()["data"]["mailboxes"][0]


@pytest.mark.asyncio
async def test_protocol_register_blocks(tmp_path: Path):
    settings = make_settings(tmp_path)
    application = create_app(settings, start_worker=True)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            await _import(http)
            queued = await http.post("/v1/jobs/register", json={"backend": "protocol", "region": "US"})
            assert queued.status_code == 200
            job_id = queued.json()["data"]["id"]
            shown = None
            for _ in range(40):
                shown = await http.get(f"/v1/jobs/{job_id}")
                status = shown.json()["data"]["status"]
                if status in {"blocked", "failed"}:
                    break
                await asyncio.sleep(0.05)
            assert shown is not None
            assert shown.json()["data"]["status"] == "blocked"
            assert shown.json()["data"]["error_class"] == "register_blocked"


@pytest.mark.asyncio
async def test_checkout_export_release(client):
    http, application = client
    await _import(http)
    store = application.state.store
    job = await store.enqueue_register(
        mailbox_id=None,
        region="US",
        backend="automation",
        sticky_minutes=30,
    )
    await store.finish_job_success(
        job["id"],
        RegisterResult(email="alice@outlook.com", status="registered", registered_at="2026-09-21T00:00:00Z"),
    )

    stats = await http.get("/v1/accounts/stats")
    assert stats.json()["data"]["registered"] == 1

    checkout = await http.post(
        "/v1/accounts/checkout",
        json={"count": 1, "ttl_seconds": 120, "leased_to": "test"},
    )
    assert checkout.status_code == 200
    account_id = checkout.json()["data"]["accounts"][0]["id"]
    assert checkout.json()["data"]["accounts"][0]["status"] == "leased"

    empty = await http.post(
        "/v1/accounts/checkout",
        json={"count": 1, "ttl_seconds": 120, "leased_to": "test"},
    )
    assert empty.status_code == 409

    released = await http.post(f"/v1/accounts/{account_id}/release")
    assert released.json()["data"]["status"] == "registered"

    exported = await http.get("/v1/accounts/export")
    assert exported.status_code == 200
    paths = exported.json()["data"]["paths"]
    assert paths
    payload = json.loads(Path(paths[0]).read_text(encoding="utf-8"))
    assert payload["email"] == "alice@outlook.com"
    assert payload["console"] == "https://console.typesafe.ai/"
    assert Path(paths[0]).suffix == ".json"
