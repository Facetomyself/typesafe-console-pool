from __future__ import annotations

import pytest

from app.config import Settings
from app.errors import RegionForbidden
from app.services.cliproxy import CliproxyManager, normalize_region, validate_sid


def test_normalize_region_rejects_rand():
    for value in ("Rand", "RAND", "RANDOM", "", "USA"):
        with pytest.raises(RegionForbidden):
            normalize_region(value)


def test_normalize_region_accepts_two_letters():
    assert normalize_region("us") == "US"


def test_validate_sid_charset():
    assert validate_sid("ts_abc_w0_l1") == "ts_abc_w0_l1"
    with pytest.raises(ValueError, match="SID"):
        validate_sid("bad-sid")


def test_build_and_rebuild_keep_sid(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "pool.sqlite",
        secrets_path=tmp_path / "secrets.json",
        exports_dir=tmp_path / "exports",
        profiles_dir=tmp_path / "profiles",
        cliproxy_user="userbase",
        cliproxy_pass="secret",
        cliproxy_host="us.arxlabs.io",
        cliproxy_port=3010,
    )
    manager = CliproxyManager(settings)
    first = manager.build_lease(region="US", sticky_minutes=30)
    rebuilt = manager.rebuild_lease(sid=first.sid, region=first.region, sticky_minutes=first.sticky_minutes)
    assert first.sid == rebuilt.sid
    assert first.account == rebuilt.account
    assert "sid-" + first.sid in first.account
    assert "-region-US-" in first.account
    second = manager.build_lease(region="US", sticky_minutes=30)
    assert second.sid != first.sid
