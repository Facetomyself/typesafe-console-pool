from __future__ import annotations

from pathlib import Path

import pytest

from app.services.mailbox import parse_mailbox_path, parse_mailbox_txt


SAMPLE = """卡密导出
# comment

alice@outlook.com----unused----client-a----refresh-a
bob@outlook.com----unused----client-b----refresh-b
"""


def test_parse_mailbox_txt_skips_header_and_comments():
    rows = parse_mailbox_txt(SAMPLE)
    assert [row["email"] for row in rows] == ["alice@outlook.com", "bob@outlook.com"]
    assert "password" not in rows[0]
    assert rows[0]["client_id"] == "client-a"
    assert rows[1]["refresh_token"] == "refresh-b"


def test_parse_mailbox_txt_rejects_wrong_arity():
    with pytest.raises(ValueError, match="invalid Outlook record"):
        parse_mailbox_txt("only-two----fields\n")


def test_parse_mailbox_txt_rejects_empty():
    with pytest.raises(ValueError, match="no Outlook records"):
        parse_mailbox_txt("# only comments\n")


def test_parse_mailbox_path(tmp_path: Path):
    target = tmp_path / "mailbox.txt"
    target.write_text(SAMPLE, encoding="utf-8")
    rows = parse_mailbox_path(target)
    assert len(rows) == 2
