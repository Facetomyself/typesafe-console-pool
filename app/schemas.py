"""Request and response models. extra=forbid."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Envelope(StrictModel):
    ok: bool
    data: Any | None = None
    error: dict[str, Any] | None = None


MailboxStatus = Literal["ready", "in_use", "bound", "cooling", "disabled"]
MailboxHealth = Literal["unknown", "ok", "cooling", "dead"]
LeaseState = Literal["held", "released", "rotated", "failed"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "blocked"]
JobType = Literal["register", "keepalive"]
BackendName = Literal["automation", "protocol"]
AccountStatus = Literal["registered", "leased", "disabled", "retired"]


class MailboxImportRequest(StrictModel):
    path: str | None = None
    text: str | None = None
    source: str | None = Field(default=None, max_length=200)


class MailboxPatchRequest(StrictModel):
    status: Literal["ready", "disabled"] | None = None
    notes: str | None = Field(default=None, max_length=500)
    clear_cooldown: bool = False


class MailboxCheckRequest(StrictModel):
    limit: int = Field(default=20, ge=1, le=100)


class MailboxOut(StrictModel):
    id: str
    email: str
    status: MailboxStatus
    imported_at: str
    health: MailboxHealth = "unknown"
    fail_count: int = 0
    cooldown_until: str | None = None
    last_checked_at: str | None = None
    last_ok_at: str | None = None
    last_error: str | None = None
    notes: str = ""
    source: str | None = None
    bound_account_id: str | None = None


class LeaseCreateRequest(StrictModel):
    region: str | None = Field(default=None, min_length=2, max_length=2)
    sticky_minutes: int | None = Field(default=None, ge=1, le=120)


class LeaseOut(StrictModel):
    id: str
    sid: str
    region: str
    sticky_minutes: int
    state: LeaseState
    job_id: str | None = None
    created_at: str
    last_ip_hash: str | None = None


class RegisterJobRequest(StrictModel):
    mailbox_id: str | None = None
    region: str | None = Field(default=None, min_length=2, max_length=2)
    backend: BackendName | None = None
    sticky_minutes: int | None = Field(default=None, ge=1, le=120)


class RegisterBatchRequest(StrictModel):
    count: int = Field(ge=1, le=20)
    region: str | None = Field(default=None, min_length=2, max_length=2)
    backend: BackendName | None = None
    sticky_minutes: int | None = Field(default=None, ge=1, le=120)


class KeepaliveJobRequest(StrictModel):
    account_id: str
    backend: BackendName | None = None


class KeepaliveDueRequest(StrictModel):
    limit: int = Field(default=10, ge=1, le=50)


class RiskPolicyPatchRequest(StrictModel):
    register_min_interval_seconds: int | None = Field(default=None, ge=0, le=86400)
    register_daily_cap: int | None = Field(default=None, ge=1, le=1000)
    mailbox_fail_cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)
    mailbox_max_fail: int | None = Field(default=None, ge=1, le=20)
    keepalive_interval_hours: float | None = Field(default=None, ge=1, le=168)
    keepalive_jitter_minutes: int | None = Field(default=None, ge=0, le=720)
    sticky_minutes: int | None = Field(default=None, ge=1, le=120)
    checkout_requires_healthy_mailbox: bool | None = None


class JobOut(StrictModel):
    id: str
    type: JobType
    mailbox_id: str | None
    email: str | None = None
    lease_id: str | None
    backend: BackendName
    status: JobStatus
    error_class: str | None = None
    error_message: str | None = None
    created_at: str
    finished_at: str | None = None
    account_id: str | None = None
    risk_snapshot: dict[str, Any] | None = None
    lease: LeaseOut | None = None


class AccountOut(StrictModel):
    id: str
    email: str
    status: AccountStatus
    job_id: str | None
    registered_at: str
    console: str
    leased_to: str | None = None
    lease_expires_at: str | None = None
    region: str | None = None
    last_keepalive_at: str | None = None
    keepalive_due_at: str | None = None
    risk_score: int = 0
    keep_notes: str = ""


class CheckoutRequest(StrictModel):
    count: int = Field(default=1, ge=1, le=20)
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    leased_to: str = Field(default="caller", min_length=1, max_length=64)


class ControlRequest(StrictModel):
    reason: str = Field(default="", max_length=500)


class StatsOut(StrictModel):
    registered: int
    leased: int
    disabled: int
    retired: int
    mailboxes_ready: int
    mailboxes_bound: int = 0
    mailboxes_cooling: int = 0
    jobs_queued: int
    jobs_running: int
    keepalive_due: int = 0
