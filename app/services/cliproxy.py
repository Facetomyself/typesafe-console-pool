"""Cliproxy sticky lease builder. SID is [A-Za-z0-9_]; Rand is rejected."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import quote

from app.errors import ProxyAdmissionFailed, RegionForbidden

SID_RE = re.compile(r"^[A-Za-z0-9_]+$")
ROUTED_ACCOUNT_RE = re.compile(
    r"^(?P<base_user>.+)-region-(?P<region>[A-Za-z]{2})"
    r"(?:-st-(?P<state>[A-Za-z0-9_]+))?"
    r"(?:-sid-(?P<sid>[A-Za-z0-9_]+))?"
    r"(?:-t-(?P<sticky_minutes>[1-9][0-9]{0,2}))?$"
)


def normalize_region(region: str) -> str:
    value = (region or "").strip().upper()
    if value in {"", "RAND", "RANDOM"}:
        raise RegionForbidden("Cliproxy region must be an explicit two-letter code")
    if not re.fullmatch(r"[A-Z]{2}", value):
        raise RegionForbidden("Cliproxy region must be an explicit two-letter code")
    return value


def validate_sid(sid: str) -> str:
    if not SID_RE.fullmatch(sid):
        raise ValueError("SID must match [A-Za-z0-9_]")
    return sid


@dataclass(frozen=True)
class RouteTemplate:
    base_user: str
    region: str
    state: str | None = None

    def sticky_account(self, *, sid: str, sticky_minutes: int) -> str:
        parts = [self.base_user, "region", self.region]
        if self.state:
            parts.extend(("st", self.state))
        parts.extend(("sid", sid, "t", str(sticky_minutes)))
        return "-".join(parts)


def parse_route_template(account: str) -> RouteTemplate | None:
    match = ROUTED_ACCOUNT_RE.fullmatch(account)
    if match is None:
        return None
    sticky = match.group("sticky_minutes")
    if sticky is not None and int(sticky) > 120:
        return None
    return RouteTemplate(
        base_user=match.group("base_user"),
        region=match.group("region").upper(),
        state=match.group("state"),
    )


def generate_sid(prefix: str, run_token: str, worker_id: int, sequence: int) -> str:
    sid = f"{prefix}_{run_token}_w{worker_id}_l{sequence}"
    return validate_sid(sid)


def generate_run_token(run_id: str) -> str:
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8]


def hash_ip(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class StickyLease:
    sid: str
    region: str
    sticky_minutes: int
    account: str
    host: str
    port: int
    url: str

    def public(self) -> dict[str, object]:
        return {
            "sid": self.sid,
            "region": self.region,
            "sticky_minutes": self.sticky_minutes,
            "host": self.host,
            "port": self.port,
        }


class CliproxyManager:
    def __init__(self, settings):
        self.settings = settings
        self._sequence = 0
        self._run_token = generate_run_token(settings.sid_prefix)
        self._opens = 0
        self.circuit_open = False

    def next_sid(self, worker_id: int = 0) -> str:
        self._sequence += 1
        return generate_sid(self.settings.sid_prefix, self._run_token, worker_id, self._sequence)

    def build_lease(self, *, region: str | None, sticky_minutes: int | None, worker_id: int = 0) -> StickyLease:
        if not self.settings.cliproxy_configured:
            raise ProxyAdmissionFailed("Cliproxy credentials are missing")
        if self.circuit_open:
            raise ProxyAdmissionFailed("Cliproxy circuit is open")
        chosen_region = normalize_region(region or self.settings.default_region)
        minutes = sticky_minutes or self.settings.sticky_minutes
        if minutes < 1 or minutes > 120:
            raise RegionForbidden("sticky_minutes must be 1-120")
        sid = self.next_sid(worker_id)
        template = parse_route_template(self.settings.cliproxy_account) if self.settings.cliproxy_account else None
        if template is not None:
            if template.region and template.region != chosen_region:
                chosen_region = normalize_region(chosen_region)
            account = RouteTemplate(template.base_user, chosen_region, template.state).sticky_account(
                sid=sid, sticky_minutes=minutes
            )
        else:
            base = self.settings.cliproxy_user
            if not base:
                raise ProxyAdmissionFailed("Cliproxy base user is missing")
            account = f"{base}-region-{chosen_region}-sid-{sid}-t-{minutes}"
        url = (
            f"http://{quote(account, safe='')}:{quote(self.settings.cliproxy_pass, safe='')}"
            f"@{self.settings.cliproxy_host}:{self.settings.cliproxy_port}"
        )
        return StickyLease(
            sid=sid,
            region=chosen_region,
            sticky_minutes=minutes,
            account=account,
            host=self.settings.cliproxy_host,
            port=self.settings.cliproxy_port,
            url=url,
        )

    def rebuild_lease(self, *, sid: str, region: str, sticky_minutes: int) -> StickyLease:
        if not self.settings.cliproxy_configured:
            raise ProxyAdmissionFailed("Cliproxy credentials are missing")
        chosen_region = normalize_region(region)
        validate_sid(sid)
        minutes = sticky_minutes
        template = parse_route_template(self.settings.cliproxy_account) if self.settings.cliproxy_account else None
        if template is not None:
            account = RouteTemplate(template.base_user, chosen_region, template.state).sticky_account(
                sid=sid, sticky_minutes=minutes
            )
        else:
            base = self.settings.cliproxy_user
            if not base:
                raise ProxyAdmissionFailed("Cliproxy base user is missing")
            account = f"{base}-region-{chosen_region}-sid-{sid}-t-{minutes}"
        url = (
            f"http://{quote(account, safe='')}:{quote(self.settings.cliproxy_pass, safe='')}"
            f"@{self.settings.cliproxy_host}:{self.settings.cliproxy_port}"
        )
        return StickyLease(
            sid=sid,
            region=chosen_region,
            sticky_minutes=minutes,
            account=account,
            host=self.settings.cliproxy_host,
            port=self.settings.cliproxy_port,
            url=url,
        )

    def snapshot(self) -> dict[str, object]:
        payload = self.settings.redacted_proxy()
        payload.update({"circuit_open": self.circuit_open, "lease_sequence": self._sequence, "opens": self._opens})
        return payload
