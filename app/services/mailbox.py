"""Outlook four-field mailbox txt parser."""

from __future__ import annotations

from pathlib import Path


def parse_mailbox_txt(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#") or raw == "卡密导出":
            continue
        fields = raw.split("----")
        if len(fields) != 4 or not all(fields):
            raise ValueError(
                f"invalid Outlook record at line {number}: expect email----password----client_id----refresh_token"
            )
        email, _password, client_id, refresh_token = fields
        records.append(
            {
                "email": email.strip(),
                "client_id": client_id.strip(),
                "refresh_token": refresh_token.strip(),
            }
        )
    if not records:
        raise ValueError("mailbox txt contains no Outlook records")
    return records


def parse_mailbox_path(path: Path) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"cannot read mailbox file: {path}") from exc
    return parse_mailbox_txt(text)
