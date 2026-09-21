"""HTTP API v1."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app import __version__
from app.schemas import (
    CheckoutRequest,
    ControlRequest,
    Envelope,
    KeepaliveDueRequest,
    KeepaliveJobRequest,
    LeaseCreateRequest,
    MailboxCheckRequest,
    MailboxImportRequest,
    MailboxPatchRequest,
    RegisterBatchRequest,
    RegisterJobRequest,
    RiskPolicyPatchRequest,
)
from app.store import PoolStore

router = APIRouter()


def store(request: Request) -> PoolStore:
    return request.app.state.store


def ok(data) -> Envelope:
    return Envelope(ok=True, data=data, error=None)


@router.get("/health", response_model=Envelope, tags=["health"])
async def health():
    return ok({"status": "ok"})


@router.get("/ready", response_model=Envelope, tags=["health"])
async def ready(request: Request):
    snapshot = await store(request).health()
    ready_ok = bool(snapshot["cliproxy"]["configured"] and snapshot["worker_running"])
    return Envelope(ok=ready_ok, data={"ready": ready_ok, **snapshot}, error=None if ready_ok else {"code": "not_ready"})


@router.get("/v1/meta", response_model=Envelope, tags=["health"])
async def meta(request: Request):
    settings = request.app.state.settings
    return ok(
        {
            "version": __version__,
            "backend": settings.default_backend,
            "default_region": settings.default_region,
            "sticky_minutes": settings.sticky_minutes,
            "queue_workers": settings.queue_workers,
        }
    )


@router.get("/v1/risk/policy", response_model=Envelope, tags=["risk"])
async def get_risk_policy(request: Request):
    return ok(await store(request).get_risk_policy())


@router.patch("/v1/risk/policy", response_model=Envelope, tags=["risk"])
async def patch_risk_policy(request: Request, body: RiskPolicyPatchRequest):
    patch = body.model_dump(exclude_none=True)
    return ok(await store(request).set_risk_policy(patch))


@router.get("/v1/risk/events", response_model=Envelope, tags=["risk"])
async def list_risk_events(
    request: Request,
    kind: str | None = Query(default=None),
    mailbox_id: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    rows = await store(request).list_risk_events(
        kind=kind,
        mailbox_id=mailbox_id,
        account_id=account_id,
        limit=limit,
    )
    return ok({"events": rows, "count": len(rows)})


@router.post("/v1/mailboxes/import", response_model=Envelope, tags=["mailboxes"])
async def import_mailboxes(request: Request, body: MailboxImportRequest):
    rows = await store(request).import_mailboxes(path=body.path, text=body.text, source=body.source)
    return ok({"mailboxes": rows, "count": len(rows)})


@router.get("/v1/mailboxes", response_model=Envelope, tags=["mailboxes"])
async def list_mailboxes(
    request: Request,
    status: str | None = Query(default=None),
    health: str | None = Query(default=None),
):
    return ok({"mailboxes": await store(request).list_mailboxes(status, health)})


@router.get("/v1/mailboxes/stats", response_model=Envelope, tags=["mailboxes"])
async def mailbox_stats(request: Request):
    return ok(await store(request).mailbox_stats())


@router.post("/v1/mailboxes/check", response_model=Envelope, tags=["mailboxes"])
async def check_mailboxes(request: Request, body: MailboxCheckRequest):
    rows = await store(request).check_mailboxes(limit=body.limit)
    return ok({"mailboxes": rows, "count": len(rows)})


@router.get("/v1/mailboxes/{mailbox_id}", response_model=Envelope, tags=["mailboxes"])
async def get_mailbox(request: Request, mailbox_id: str):
    return ok(await store(request).get_mailbox(mailbox_id))


@router.patch("/v1/mailboxes/{mailbox_id}", response_model=Envelope, tags=["mailboxes"])
async def patch_mailbox(request: Request, mailbox_id: str, body: MailboxPatchRequest):
    return ok(
        await store(request).patch_mailbox(
            mailbox_id,
            status=body.status,
            notes=body.notes,
            clear_cooldown=body.clear_cooldown,
        )
    )


@router.post("/v1/mailboxes/{mailbox_id}/check", response_model=Envelope, tags=["mailboxes"])
async def check_mailbox(request: Request, mailbox_id: str):
    return ok(await store(request).check_mailbox(mailbox_id))


@router.get("/v1/proxy/status", response_model=Envelope, tags=["proxy"])
async def proxy_status(request: Request):
    return ok(store(request).cliproxy.snapshot())


@router.post("/v1/proxy/leases", response_model=Envelope, tags=["proxy"])
async def create_lease(request: Request, body: LeaseCreateRequest):
    lease = store(request).cliproxy.build_lease(region=body.region, sticky_minutes=body.sticky_minutes)
    row = await store(request).persist_lease(lease)
    return ok(row)


@router.get("/v1/proxy/leases", response_model=Envelope, tags=["proxy"])
async def list_leases(request: Request, state: str | None = Query(default=None)):
    return ok({"leases": await store(request).list_leases(state)})


@router.post("/v1/proxy/leases/{lease_id}/release", response_model=Envelope, tags=["proxy"])
async def release_lease(request: Request, lease_id: str):
    return ok(await store(request).release_lease(lease_id))


@router.post("/v1/proxy/leases/{lease_id}/rotate", response_model=Envelope, tags=["proxy"])
async def rotate_lease(request: Request, lease_id: str):
    return ok(await store(request).rotate_lease(lease_id))


@router.post("/v1/jobs/register", response_model=Envelope, tags=["jobs"])
async def register_job(request: Request, body: RegisterJobRequest):
    job = await store(request).enqueue_register(
        mailbox_id=body.mailbox_id,
        region=body.region,
        backend=body.backend,
        sticky_minutes=body.sticky_minutes,
    )
    return ok(job)


@router.post("/v1/jobs/register/batch", response_model=Envelope, tags=["jobs"])
async def register_batch(request: Request, body: RegisterBatchRequest):
    jobs = await store(request).enqueue_batch(
        count=body.count,
        region=body.region,
        backend=body.backend,
        sticky_minutes=body.sticky_minutes,
    )
    return ok({"jobs": jobs, "count": len(jobs)})


@router.post("/v1/jobs/keepalive", response_model=Envelope, tags=["jobs"])
async def keepalive_job(request: Request, body: KeepaliveJobRequest):
    return ok(await store(request).enqueue_keepalive(body.account_id, backend=body.backend))


@router.post("/v1/jobs/keepalive/due", response_model=Envelope, tags=["jobs"])
async def keepalive_due(request: Request, body: KeepaliveDueRequest | None = None):
    payload = body or KeepaliveDueRequest()
    jobs = await store(request).enqueue_keepalive_due(limit=payload.limit)
    return ok({"jobs": jobs, "count": len(jobs)})


@router.get("/v1/jobs", response_model=Envelope, tags=["jobs"])
async def list_jobs(
    request: Request,
    status: str | None = Query(default=None),
    backend: str | None = Query(default=None),
    type: str | None = Query(default=None, alias="type"),
):
    return ok({"jobs": await store(request).list_jobs(status=status, backend=backend, job_type=type)})


@router.get("/v1/jobs/{job_id}", response_model=Envelope, tags=["jobs"])
async def get_job(request: Request, job_id: str):
    return ok(await store(request).get_job(job_id))


@router.post("/v1/jobs/{job_id}/cancel", response_model=Envelope, tags=["jobs"])
async def cancel_job(request: Request, job_id: str):
    return ok(await store(request).cancel_job(job_id))


@router.post("/v1/jobs/{job_id}/retry", response_model=Envelope, tags=["jobs"])
async def retry_job(request: Request, job_id: str):
    return ok(await store(request).retry_job(job_id))


@router.get("/v1/accounts", response_model=Envelope, tags=["accounts"])
async def list_accounts(request: Request, status: str | None = Query(default=None)):
    return ok({"accounts": await store(request).list_accounts(status)})


@router.get("/v1/accounts/stats", response_model=Envelope, tags=["accounts"])
async def account_stats(request: Request):
    return ok(await store(request).stats())


@router.get("/v1/accounts/export", response_model=Envelope, tags=["accounts"])
async def export_accounts(request: Request, account_id: str | None = Query(default=None)):
    return ok(await store(request).export_accounts(account_id))


@router.get("/v1/accounts/{account_id}", response_model=Envelope, tags=["accounts"])
async def get_account(request: Request, account_id: str):
    return ok(await store(request).get_account(account_id))


@router.post("/v1/accounts/checkout", response_model=Envelope, tags=["accounts"])
async def checkout(request: Request, body: CheckoutRequest):
    rows = await store(request).checkout(body.count, body.ttl_seconds, body.leased_to)
    return ok({"accounts": rows, "count": len(rows)})


@router.post("/v1/accounts/{account_id}/release", response_model=Envelope, tags=["accounts"])
async def release_account(request: Request, account_id: str):
    return ok(await store(request).release_account(account_id))


@router.post("/v1/accounts/{account_id}/disable", response_model=Envelope, tags=["accounts"])
async def disable_account(request: Request, account_id: str):
    return ok(await store(request).disable_account(account_id))


@router.post("/v1/controls/pause", response_model=Envelope, tags=["controls"])
async def pause(request: Request, body: ControlRequest | None = None):
    payload = body or ControlRequest()
    return ok(await store(request).set_paused(True, payload.reason))


@router.post("/v1/controls/resume", response_model=Envelope, tags=["controls"])
async def resume(request: Request, body: ControlRequest | None = None):
    payload = body or ControlRequest()
    return ok(await store(request).set_paused(False, payload.reason))
