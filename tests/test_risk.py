from __future__ import annotations

from app.services.risk import (
    DEFAULT_POLICY,
    classify_mailbox_error,
    is_future,
    merge_policy,
    next_mailbox_status,
    plus_hours,
    snapshot_for_job,
)


def test_merge_policy_keeps_defaults_and_dimensions():
    merged = merge_policy({"register_daily_cap": 7, "dimensions": {"ip": "sticky only"}})
    assert merged["register_daily_cap"] == 7
    assert merged["reject_rand"] is True
    assert merged["dimensions"]["environment"] == DEFAULT_POLICY["dimensions"]["environment"]
    assert merged["dimensions"]["ip"] == "sticky only"


def test_classify_and_next_status():
    assert classify_mailbox_error("token refresh failed: invalid_grant") == "token_dead"
    assert classify_mailbox_error("imap authenticate failed") == "imap_fail"
    assert next_mailbox_status(fail_count=1, max_fail=3, classified="imap_fail") == "cooling"
    assert next_mailbox_status(fail_count=1, max_fail=3, classified="token_dead") == "disabled"
    assert next_mailbox_status(fail_count=3, max_fail=3, classified="timeout") == "disabled"


def test_snapshot_and_due_stamp():
    snap = snapshot_for_job(DEFAULT_POLICY, region="US", sticky_minutes=30, sid="ts_abc")
    assert snap["region"] == "US"
    assert snap["reject_rand"] is True
    due = plus_hours(24, jitter_minutes=90, now="2026-09-21T00:00:00Z")
    assert due.startswith("2026-09-22T")
    assert is_future("2099-01-01T00:00:00Z") is True
    assert is_future("2000-01-01T00:00:00Z") is False
