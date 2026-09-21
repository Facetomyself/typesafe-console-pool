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


MailboxStatus = Literal["ready", "in_use", "exhausted", "disabled"]
LeaseState = Literal["held", "released", "rotated", "failed"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "blocked"]
BackendName = Literal["automation", "protocol"]
AccountStatus = Literal["registered", "leased", "disabled", "retired"]


class MailboxImportRequest(StrictModel):
    path: str | None = None
    text: str | None = None


class MailboxPatchRequest(StrictModel):
    status: Literal["ready", "disabled"]


class MailboxOut(StrictModel):
    id: str
    email: str
    status: MailboxStatus
    imported_at: str


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


class JobOut(StrictModel):
    id: str
    type: str
    mailbox_id: str | None
    email: str | None = None
    lease_id: str | None
    backend: BackendName
    status: JobStatus
    error_class: str | None = None
    error_message: str | None = None
    created_at: str
    finished_at: str | None = None
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
    jobs_queued: int
    jobs_running: int
