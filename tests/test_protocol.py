from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.base import RegisterResult
from app.backends.protocol import ProtocolBackend
from app.config import Settings
from app.errors import RegisterBlocked


class _FakeConsole:
    def prove_usable(self, existing=None):
        del existing
        return {"api_key": "sk-test", "api_key_id": "key_1", "api_key_name": "healthcheck"}

    def cookie_map(self):
        return {"session": "cookie"}


def test_protocol_backend_blocked_when_repo_missing(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        protocol_root=tmp_path / "missing-protocol",
    )
    backend = ProtocolBackend(settings)
    mailbox = {"email": "alice@outlook.com", "client_id": "c", "refresh_token": "r"}
    with pytest.raises(RegisterBlocked, match="register.py not found"):
        backend.register(mailbox, "http://user:pass@host:3010", "job_test")


def test_protocol_backend_register_writes_session(tmp_path: Path, monkeypatch):
    settings = Settings(data_dir=tmp_path, protocol_root=tmp_path / "protocol")
    backend = ProtocolBackend(settings)
    fake = _FakeConsole()

    class FakeProto:
        @staticmethod
        def register_over_http(*_args, **_kwargs):
            return fake

    class FakeHttp:
        @staticmethod
        def build_console_session(*, proxy=None, impersonate="chrome"):
            del proxy, impersonate
            return object()

    monkeypatch.setattr(backend, "_load_protocol", lambda: (FakeProto, FakeHttp))
    mailbox = {"email": "alice@outlook.com", "client_id": "c", "refresh_token": "r"}
    result = backend.register(mailbox, "http://user:pass@host:3010", "job_test")
    assert isinstance(result, RegisterResult)
    assert result.status == "registered"
    stored = (tmp_path / "sessions" / "alice@outlook.com.json").read_text(encoding="utf-8")
    assert "sk-test" in stored
