from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

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


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def client(settings: Settings):
    application = create_app(settings, start_worker=False)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http, application
