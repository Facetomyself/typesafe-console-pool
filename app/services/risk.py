"""Keepalive and registration risk policy.

Dimensions follow the workspace risk-control notes: environment, IP/sticky,
and behaviour cadence. Scores are local bookkeeping, not a claim that a
live TypeSafe challenge was bypassed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_POLICY: dict[str, Any] = {
    "region_sticky": True,
    "reject_rand": True,
    "sid_charset": "[A-Za-z0-9_]",
    "sticky_minutes": 30,
    "register_min_interval_seconds": 180,
    "register_daily_cap": 40,
    "mailbox_fail_cooldown_seconds": 900,
    "mailbox_max_fail": 3,
    "keepalive_interval_hours": 24,
    "keepalive_jitter_minutes": 90,
    "reuse_same_region": True,
    "checkout_requires_healthy_mailbox": True,
    "dimensions": {
        "environment": "vanilla HTTP client over Cliproxy sticky; TLS session per job",
        "ip": "Cliproxy sticky SID per job; explicit two-letter region; rebuild from stored SID",
        "behaviour": "right-skewed register spacing; HTTP keepalive against console without burst checkout",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def plus_seconds(seconds: int, *, now: str | None = None) -> str:
    base = parse_iso(now) if now else datetime.now(timezone.utc)
    if base is None:
        base = datetime.now(timezone.utc)
    return (base + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def plus_hours(hours: float, *, jitter_minutes: int = 0, now: str | None = None) -> str:
    extra = int(jitter_minutes) * 30
    return plus_seconds(int(hours * 3600) + extra, now=now)


def is_future(value: str | None, *, now: str | None = None) -> bool:
    stamp = parse_iso(value)
    if stamp is None:
        return False
    current = parse_iso(now) if now else datetime.now(timezone.utc)
    if current is None:
        current = datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return stamp > current


def merge_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    if not raw:
        return policy
    for key, value in raw.items():
        if key == "dimensions" and isinstance(value, dict):
            policy["dimensions"].update(value)
        elif key in policy:
            policy[key] = value
    return policy


def snapshot_for_job(policy: dict[str, Any], *, region: str, sticky_minutes: int, sid: str) -> dict[str, Any]:
    return {
        "region": region,
        "sticky_minutes": sticky_minutes,
        "sid": sid,
        "region_sticky": policy.get("region_sticky", True),
        "reject_rand": policy.get("reject_rand", True),
        "register_min_interval_seconds": policy.get("register_min_interval_seconds"),
        "keepalive_interval_hours": policy.get("keepalive_interval_hours"),
        "dimensions": policy.get("dimensions", {}),
    }


def classify_mailbox_error(message: str) -> str:
    text = (message or "").lower()
    if "token refresh" in text or "access_token" in text or "invalid_grant" in text:
        return "token_dead"
    if "authenticate" in text or "imap" in text or "login" in text:
        return "imap_fail"
    if "timeout" in text:
        return "timeout"
    return "check_fail"


def next_mailbox_status(*, fail_count: int, max_fail: int, classified: str) -> str:
    if classified == "token_dead" or fail_count >= max_fail:
        return "disabled"
    return "cooling"
