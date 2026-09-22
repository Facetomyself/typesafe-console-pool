"""HTTP registration backend. Calls typesafe-console-protocol."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from app.backends.base import RegisterResult, utc_now
from app.config import Settings
from app.errors import RegisterBlocked

SAFE_EMAIL_RE = re.compile(r"[^A-Za-z0-9._@-]+")


class ProtocolBackend:
    name = "protocol"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def register(self, mailbox: dict[str, str], proxy_url: str, job_id: str) -> RegisterResult:
        del job_id
        proto, console_http = self._load_protocol()
        session = console_http.build_console_session(proxy=proxy_url or None)
        console = proto.register_over_http(
            mailbox["email"],
            mailbox["client_id"],
            mailbox["refresh_token"],
            session=session,
        )
        proof = console.prove_usable()
        if not proof.get("api_key"):
            raise RuntimeError("register did not mint a usable api key")
        self._write_session(
            mailbox["email"],
            {
                "email": mailbox["email"],
                "cookies": console.cookie_map(),
                "api_key": proof.get("api_key"),
                "api_key_id": proof.get("api_key_id"),
                "api_key_name": proof.get("api_key_name"),
                "usable": True,
            },
        )
        return RegisterResult(email=mailbox["email"], status="registered", registered_at=utc_now())

    def keepalive(self, mailbox: dict[str, str], proxy_url: str, job_id: str, account_id: str) -> RegisterResult:
        del job_id, account_id
        proto, console_http = self._load_protocol()
        session = console_http.build_console_session(proxy=proxy_url or None)
        stored = self._read_session(mailbox["email"])
        cookies = stored.get("cookies") if isinstance(stored.get("cookies"), dict) else None
        console, _status = proto.keepalive_over_http(
            mailbox["email"],
            mailbox["client_id"],
            mailbox["refresh_token"],
            cookies,
            session=session,
        )
        existing = stored.get("api_key") if isinstance(stored.get("api_key"), str) else None
        proof = console.prove_usable(existing)
        if not proof.get("api_key"):
            raise RuntimeError("keepalive did not prove a usable api key")
        self._write_session(
            mailbox["email"],
            {
                "email": mailbox["email"],
                "cookies": console.cookie_map(),
                "api_key": proof.get("api_key"),
                "api_key_id": proof.get("api_key_id") or stored.get("api_key_id"),
                "api_key_name": proof.get("api_key_name") or stored.get("api_key_name"),
                "usable": True,
            },
        )
        return RegisterResult(email=mailbox["email"], status="kept", registered_at=utc_now())

    def _protocol_root(self) -> Path:
        env = os.environ.get("TYPESAFE_PROTOCOL_ROOT")
        if env:
            return Path(env)
        if self.settings is not None and getattr(self.settings, "protocol_root", None):
            return Path(self.settings.protocol_root)
        return Path(__file__).resolve().parents[2].parent / "typesafe-console-protocol"

    def _load_protocol(self):
        root = self._protocol_root()
        scripts = root / "scripts"
        register_py = scripts / "register.py"
        if not register_py.is_file():
            raise RegisterBlocked(f"typesafe-console-protocol register.py not found at {root}")
        scripts_s = str(scripts)
        if scripts_s not in sys.path:
            sys.path.insert(0, scripts_s)
        try:
            import console_http  # type: ignore
            import register as proto  # type: ignore
        except ImportError as exc:
            raise RegisterBlocked(f"cannot import typesafe-console-protocol: {exc}") from exc
        return proto, console_http

    def _session_dir(self) -> Path:
        if self.settings is None:
            raise RegisterBlocked("protocol backend needs settings.data_dir to store console sessions")
        path = self.settings.data_dir / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _session_path(self, email: str) -> Path:
        safe = SAFE_EMAIL_RE.sub("_", email.strip().lower())
        return self._session_dir() / f"{safe}.json"

    def _read_session(self, email: str) -> dict:
        path = self._session_path(email)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_session(self, email: str, payload: dict) -> None:
        path = self._session_path(email)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
