"""Register backend contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RegisterResult:
    email: str
    status: str
    registered_at: str
    console: str = "https://console.typesafe.ai/"


class RegisterBackend(Protocol):
    name: str

    def register(self, mailbox: dict[str, str], proxy_url: str, job_id: str) -> RegisterResult:
        ...
