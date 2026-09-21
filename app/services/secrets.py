"""Local mailbox secrets store. Tokens never enter SQLite or API responses."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock


class SecretStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()

    def _read(self) -> dict:
        if not self.path.is_file():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def put(self, mailbox_id: str, client_id: str, refresh_token: str) -> None:
        with self._lock:
            data = self._read()
            data[mailbox_id] = {"client_id": client_id, "refresh_token": refresh_token}
            self._write(data)

    def get(self, mailbox_id: str) -> dict[str, str]:
        with self._lock:
            data = self._read()
        item = data.get(mailbox_id)
        if not item:
            raise KeyError(mailbox_id)
        return item

    def delete(self, mailbox_id: str) -> None:
        with self._lock:
            data = self._read()
            data.pop(mailbox_id, None)
            self._write(data)
