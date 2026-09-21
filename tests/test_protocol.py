from __future__ import annotations

import pytest

from app.backends.protocol import ProtocolBackend
from app.errors import RegisterBlocked


def test_protocol_backend_is_blocked():
    backend = ProtocolBackend()
    with pytest.raises(RegisterBlocked, match="still recon"):
        backend.register(
            {"email": "alice@outlook.com", "client_id": "c", "refresh_token": "r"},
            "http://user:pass@host:3010",
            "job_test",
        )
