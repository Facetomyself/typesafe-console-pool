"""Minimal IMAP XOAUTH2 OTP poller for TypeSafe mail."""

from __future__ import annotations

import html
import imaplib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from app.errors import OtpTimeout

TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
IMAP_HOST = "outlook.office365.com"
IMAP_SCOPE = "offline_access https://outlook.office.com/IMAP.AccessAsUser.All"
CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
MAIL_MATCH = re.compile(r"typesafe|console\.typesafe", re.IGNORECASE)


def refresh_access_token(client_id: str, refresh_token: str) -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": IMAP_SCOPE,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"token refresh failed: {detail}") from exc
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("token refresh returned no access_token")
    return token


def imap_login(email: str, access_token: str) -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=20)
    payload = f"user={email}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")
    client.authenticate("XOAUTH2", lambda _: payload)
    return client


def message_text(raw: bytes) -> tuple[str, datetime | None]:
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    parts: list[str] = []
    for part in parsed.walk():
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        content = part.get_content()
        parts.append(content if isinstance(content, str) else str(content))
    body = "\n".join(parts)
    subject = str(make_header(decode_header(parsed.get("Subject", ""))))
    sender = str(make_header(decode_header(parsed.get("From", ""))))
    received = parsed.get("Date", "")
    try:
        date = parsedate_to_datetime(received)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        received_at = date.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        received_at = None
    text = html.unescape(re.sub(r"<[^>]+>", " ", f"{subject} {sender} {body}"))
    return text, received_at


def folder_codes(client: imaplib.IMAP4_SSL, folder: str, after: datetime, limit: int = 20) -> list[str]:
    status, _ = client.select(folder, readonly=True)
    if status != "OK":
        return []
    status, values = client.uid("search", None, "ALL")
    if status != "OK":
        return []
    found: list[str] = []
    for uid in reversed((values[0] or b"").split()[-limit:]):
        status, fetched = client.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK":
            continue
        raw = b"".join(item[1] for item in fetched if isinstance(item, tuple) and isinstance(item[1], bytes))
        if not raw:
            continue
        text, received_at = message_text(raw)
        if received_at is not None and received_at < after:
            continue
        if not MAIL_MATCH.search(text):
            continue
        match = CODE_RE.search(text)
        if match:
            found.append(match.group(1))
    return found


def poll_otp(email: str, client_id: str, refresh_token: str, after_iso: str, timeout: int = 180, interval: int = 5) -> str:
    after = datetime.fromisoformat(after_iso.replace("Z", "+00:00"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        token = refresh_access_token(client_id, refresh_token)
        client = imap_login(email, token)
        try:
            codes = folder_codes(client, "INBOX", after)
            if not codes:
                codes = folder_codes(client, "Junk", after)
            if codes:
                return codes[0]
        finally:
            try:
                client.logout()
            except Exception:
                pass
        time.sleep(interval)
    raise OtpTimeout("no verification code")
