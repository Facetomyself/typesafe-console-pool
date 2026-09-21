from __future__ import annotations

import pytest

from app.backends.protocol import ProtocolBackend
from app.errors import RegisterBlocked


def test_protocol_backend_is_blocked():
    backend = ProtocolBackend()
    mailbox = {"email": "alice@outlook.com", "client_id": "c", "refresh_token": "r"}
    with pytest.raises(RegisterBlocked, match="still recon"):
        backend.register(mailbox, "http://user:pass@host:3010", "job_test")
    with pytest.raises(RegisterBlocked, match="keepalive is still recon"):
        backend.keepalive(mailbox, "http://user:pass@host:3010", "job_test", "ac_test")
