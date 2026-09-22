"""Service settings. Cliproxy secrets stay in storage/proxy-usage/.env."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROXY_ENV = Path(r"D:\reverse_ENV\storage\proxy-usage\.env")
DEFAULT_FIREFOX = Path(r"D:\reverse_ENV\tools\ruyipage\runtimes\151-proxy\firefox\firefox.exe")
DEFAULT_PROTOCOL_ROOT = PROJECT_ROOT.parent / "typesafe-console-protocol"


def _clean(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_env_file(path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path is None or not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = _clean(value)
    return values


def _get(env: dict[str, str], key: str, default: str = "") -> str:
    return os.environ.get(key) or env.get(key) or default


def _get_int(env: dict[str, str], key: str, default: int) -> int:
    raw = os.environ.get(key) or env.get(key)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8091
    data_dir: Path = PROJECT_ROOT / "data"
    database_path: Path = PROJECT_ROOT / "data" / "pool.sqlite"
    secrets_path: Path = PROJECT_ROOT / "data" / "secrets.json"
    exports_dir: Path = PROJECT_ROOT / "data" / "exports"
    profiles_dir: Path = PROJECT_ROOT / "data" / "profiles"
    proxy_env_path: Path = DEFAULT_PROXY_ENV
    firefox_path: Path = DEFAULT_FIREFOX
    protocol_root: Path = DEFAULT_PROTOCOL_ROOT
    default_backend: str = "protocol"
    default_region: str = "US"
    sticky_minutes: int = 30
    queue_workers: int = 1
    cliproxy_account: str = ""
    cliproxy_user: str = ""
    cliproxy_pass: str = ""
    cliproxy_host: str = "us.arxlabs.io"
    cliproxy_port: int = 3010
    cliproxy_pre_proxy: str = ""
    sid_prefix: str = "ts"

    @property
    def cliproxy_configured(self) -> bool:
        return bool((self.cliproxy_account or self.cliproxy_user) and self.cliproxy_pass)

    def redacted_proxy(self) -> dict[str, object]:
        account = self.cliproxy_account or self.cliproxy_user
        mode = "route_template" if self.cliproxy_account else "generated_sticky" if self.cliproxy_user else "missing"
        return {
            "configured": self.cliproxy_configured,
            "mode": mode,
            "host": self.cliproxy_host,
            "port": self.cliproxy_port,
            "account_present": bool(account),
            "pre_proxy_present": bool(self.cliproxy_pre_proxy),
            "region": self.default_region,
            "sticky_minutes": self.sticky_minutes,
        }

    @classmethod
    def load(cls, data_dir: Path | None = None) -> "Settings":
        root = data_dir or PROJECT_ROOT / "data"
        local: dict[str, object] = {}
        config_path = root / "config.json"
        if config_path.is_file():
            local = json.loads(config_path.read_text(encoding="utf-8"))
        proxy_env_path = Path(str(local.get("proxy_env_path") or DEFAULT_PROXY_ENV))
        env = read_env_file(proxy_env_path)
        region = str(local.get("default_region") or _get(env, "CLIPROXY_REGION", "US")).upper()
        sticky = int(local.get("sticky_minutes") or _get_int(env, "CLIPROXY_STICKY_MINUTES", 30))
        return cls(
            host=str(local.get("host") or "127.0.0.1"),
            port=int(local.get("port") or 8091),
            data_dir=root,
            database_path=root / "pool.sqlite",
            secrets_path=root / "secrets.json",
            exports_dir=root / "exports",
            profiles_dir=root / "profiles",
            proxy_env_path=proxy_env_path,
            firefox_path=Path(str(local.get("firefox_path") or DEFAULT_FIREFOX)),
            protocol_root=Path(str(local.get("protocol_root") or DEFAULT_PROTOCOL_ROOT)),
            default_backend=str(local.get("default_backend") or "protocol"),
            default_region=region,
            sticky_minutes=sticky,
            queue_workers=int(local.get("queue_workers") or 1),
            cliproxy_account=_get(env, "CLIPROXY_ACCOUNT"),
            cliproxy_user=_get(env, "CLIPROXY_USER"),
            cliproxy_pass=_get(env, "CLIPROXY_PASS"),
            cliproxy_host=_get(env, "CLIPROXY_HOST", "us.arxlabs.io"),
            cliproxy_port=_get_int(env, "CLIPROXY_PORT", 3010),
            cliproxy_pre_proxy=_get(env, "CLIPROXY_PRE_PROXY"),
            sid_prefix=str(local.get("sid_prefix") or "ts"),
        )
