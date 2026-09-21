"""Stable API error classes."""

from __future__ import annotations


class PoolError(RuntimeError):
    code = "pool_error"
    status_code = 400

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        if code:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class MailboxInvalid(PoolError):
    code = "mailbox_invalid"
    status_code = 400


class MailboxInUse(PoolError):
    code = "mailbox_in_use"
    status_code = 409


class ProxyAdmissionFailed(PoolError):
    code = "proxy_admission_failed"
    status_code = 502


class RegionForbidden(PoolError):
    code = "region_forbidden"
    status_code = 400


class RegisterBlocked(PoolError):
    code = "register_blocked"
    status_code = 409


class OtpTimeout(PoolError):
    code = "otp_timeout"
    status_code = 504


class AccountNotAvailable(PoolError):
    code = "account_not_available"
    status_code = 409


class NotFound(PoolError):
    code = "not_found"
    status_code = 404


class Conflict(PoolError):
    code = "conflict"
    status_code = 409


class Paused(PoolError):
    code = "paused"
    status_code = 409
