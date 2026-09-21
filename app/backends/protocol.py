"""Protocol HTTP backend. Blocked until request shape is frozen."""

from __future__ import annotations

from app.backends.base import RegisterResult
from app.errors import RegisterBlocked


class ProtocolBackend:
    name = "protocol"

    def register(self, mailbox: dict[str, str], proxy_url: str, job_id: str) -> RegisterResult:
        del mailbox, proxy_url, job_id
        raise RegisterBlocked(
            "HTTP registration is still recon: request shape is not frozen in typesafe-console-protocol"
        )

    def keepalive(self, mailbox: dict[str, str], proxy_url: str, job_id: str, account_id: str) -> RegisterResult:
        del mailbox, proxy_url, job_id, account_id
        raise RegisterBlocked(
            "HTTP keepalive is still recon: request shape is not frozen in typesafe-console-protocol"
        )
