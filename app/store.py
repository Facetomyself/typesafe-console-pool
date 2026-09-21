"""Account pool store: mailboxes, jobs, leases, accounts, risk ledger."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.backends.base import utc_now
from app.config import Settings
from app.db import Database
from app.errors import (
    AccountNotAvailable,
    Conflict,
    MailboxInUse,
    MailboxInvalid,
    MailboxUnhealthy,
    NotFound,
    Paused,
    PoolError,
    RateLimited,
    RegisterBlocked,
)
from app.services.cliproxy import CliproxyManager, StickyLease
from app.services.mailbox import parse_mailbox_path, parse_mailbox_txt
from app.services.otp import probe_mailbox
from app.services.risk import (
    DEFAULT_POLICY,
    classify_mailbox_error,
    is_future,
    merge_policy,
    next_mailbox_status,
    plus_hours,
    plus_seconds,
    snapshot_for_job,
)
from app.services.secrets import SecretStore


MAILBOX_COLUMNS = (
    "id, email, status, imported_at, health, fail_count, cooldown_until, "
    "last_checked_at, last_ok_at, last_error, notes, source, bound_account_id"
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class PoolStore:
    def __init__(self, settings: Settings, database: Database, secrets: SecretStore, cliproxy: CliproxyManager):
        self.settings = settings
        self.db = database
        self.secrets = secrets
        self.cliproxy = cliproxy
        self.worker_running = False

    async def ensure_controls(self) -> None:
        existing = await self.db.fetchone("SELECT name FROM controls WHERE name='register'")
        if existing is None:
            await self.db.execute(
                "INSERT INTO controls(name, paused, reason, updated_at) VALUES('register', 0, '', ?)",
                (utc_now(),),
            )
        policy = await self.db.fetchone("SELECT id FROM risk_policy WHERE id=1")
        if policy is None:
            await self.db.execute(
                "INSERT INTO risk_policy(id, payload, updated_at) VALUES(1, ?, ?)",
                (json.dumps(DEFAULT_POLICY, ensure_ascii=False), utc_now()),
            )

    async def get_risk_policy(self) -> dict:
        await self.ensure_controls()
        row = await self.db.fetchone("SELECT payload, updated_at FROM risk_policy WHERE id=1")
        payload = json.loads(row["payload"]) if row else DEFAULT_POLICY
        return {"policy": merge_policy(payload), "updated_at": row["updated_at"] if row else utc_now()}

    async def set_risk_policy(self, patch: dict) -> dict:
        current = await self.get_risk_policy()
        merged = merge_policy({**current["policy"], **patch})
        now = utc_now()
        await self.db.execute(
            "UPDATE risk_policy SET payload=?, updated_at=? WHERE id=1",
            (json.dumps(merged, ensure_ascii=False), now),
        )
        return {"policy": merged, "updated_at": now}

    async def health(self) -> dict:
        await self.ensure_controls()
        paused = await self.is_paused()
        policy = await self.get_risk_policy()
        return {
            "database": True,
            "cliproxy": self.settings.redacted_proxy(),
            "worker_running": self.worker_running,
            "paused": paused,
            "risk": {
                "register_min_interval_seconds": policy["policy"]["register_min_interval_seconds"],
                "register_daily_cap": policy["policy"]["register_daily_cap"],
                "keepalive_interval_hours": policy["policy"]["keepalive_interval_hours"],
            },
        }

    async def is_paused(self) -> bool:
        row = await self.db.fetchone("SELECT paused FROM controls WHERE name='register'")
        return bool(row and row["paused"])

    async def set_paused(self, paused: bool, reason: str = "") -> dict:
        await self.ensure_controls()
        await self.db.execute(
            "UPDATE controls SET paused=?, reason=?, updated_at=? WHERE name='register'",
            (1 if paused else 0, reason, utc_now()),
        )
        return {"paused": paused, "reason": reason}

    async def add_risk_event(
        self,
        *,
        kind: str,
        message: str,
        severity: str = "info",
        mailbox_id: str | None = None,
        account_id: str | None = None,
        job_id: str | None = None,
        detail: dict | None = None,
    ) -> dict:
        event_id = _id("ev")
        await self.db.execute(
            "INSERT INTO risk_events(id, created_at, kind, severity, mailbox_id, account_id, job_id, message, detail) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                utc_now(),
                kind,
                severity,
                mailbox_id,
                account_id,
                job_id,
                message,
                json.dumps(detail, ensure_ascii=False) if detail else None,
            ),
        )
        return await self.db.fetchone("SELECT * FROM risk_events WHERE id=?", (event_id,))

    async def list_risk_events(
        self,
        *,
        kind: str | None = None,
        mailbox_id: str | None = None,
        account_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        sql = "SELECT * FROM risk_events WHERE 1=1"
        params: list = []
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if mailbox_id:
            sql += " AND mailbox_id=?"
            params.append(mailbox_id)
        if account_id:
            sql += " AND account_id=?"
            params.append(account_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = await self.db.fetchall(sql, tuple(params))
        for row in rows:
            if row.get("detail"):
                try:
                    row["detail"] = json.loads(row["detail"])
                except json.JSONDecodeError:
                    pass
        return rows

    async def import_mailboxes(self, *, path: str | None, text: str | None, source: str | None = None) -> list[dict]:
        if bool(path) == bool(text):
            raise MailboxInvalid("provide exactly one of path or text")
        try:
            records = parse_mailbox_path(Path(path)) if path else parse_mailbox_txt(text or "")
        except ValueError as exc:
            raise MailboxInvalid(str(exc)) from exc
        origin = source or (str(path) if path else "inline")
        created: list[dict] = []
        inserted = 0
        updated = 0
        for record in records:
            existing = await self.db.fetchone(
                f"SELECT {MAILBOX_COLUMNS} FROM mailboxes WHERE email=?",
                (record["email"],),
            )
            if existing:
                if existing["status"] == "in_use":
                    raise MailboxInUse(f"mailbox already in use: {record['email']}")
                mailbox_id = existing["id"]
                if existing["status"] == "bound":
                    status = "bound"
                elif existing["status"] in {"disabled", "cooling", "ready"}:
                    status = "ready"
                else:
                    status = existing["status"]
                await self.db.execute(
                    "UPDATE mailboxes SET status=?, imported_at=?, health='unknown', last_error=NULL, "
                    "fail_count=0, cooldown_until=NULL, source=? WHERE id=?",
                    (status, utc_now(), origin, mailbox_id),
                )
                updated += 1
            else:
                mailbox_id = _id("mb")
                await self.db.execute(
                    "INSERT INTO mailboxes(id, email, status, imported_at, health, fail_count, source) "
                    "VALUES(?,?,?,?, 'unknown', 0, ?)",
                    (mailbox_id, record["email"], "ready", utc_now(), origin),
                )
                inserted += 1
            self.secrets.put(mailbox_id, record["client_id"], record["refresh_token"])
            created.append(await self.get_mailbox(mailbox_id))
        await self.add_risk_event(
            kind="mailbox_import",
            message=f"imported {len(created)} mailboxes",
            detail={"inserted": inserted, "updated": updated, "source": origin},
        )
        return created

    async def list_mailboxes(self, status: str | None = None, health: str | None = None) -> list[dict]:
        sql = f"SELECT {MAILBOX_COLUMNS} FROM mailboxes WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if health:
            sql += " AND health=?"
            params.append(health)
        sql += " ORDER BY imported_at"
        return await self.db.fetchall(sql, tuple(params))

    async def get_mailbox(self, mailbox_id: str) -> dict:
        row = await self.db.fetchone(
            f"SELECT {MAILBOX_COLUMNS} FROM mailboxes WHERE id=?",
            (mailbox_id,),
        )
        if not row:
            raise NotFound("mailbox not found")
        return row

    async def mailbox_stats(self) -> dict:
        async def count(status: str | None = None, health: str | None = None) -> int:
            sql = "SELECT COUNT(*) AS n FROM mailboxes WHERE 1=1"
            params: list = []
            if status:
                sql += " AND status=?"
                params.append(status)
            if health:
                sql += " AND health=?"
                params.append(health)
            row = await self.db.fetchone(sql, tuple(params))
            return int(row["n"] if row else 0)

        cooling = await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM mailboxes WHERE status='cooling' OR health='cooling'"
        )
        return {
            "ready": await count("ready"),
            "in_use": await count("in_use"),
            "bound": await count("bound"),
            "cooling": int(cooling["n"] if cooling else 0),
            "disabled": await count("disabled"),
            "healthy": await count(health="ok"),
            "unknown": await count(health="unknown"),
            "total": await count(),
        }

    async def patch_mailbox(
        self,
        mailbox_id: str,
        *,
        status: str | None = None,
        notes: str | None = None,
        clear_cooldown: bool = False,
    ) -> dict:
        row = await self.get_mailbox(mailbox_id)
        if status:
            if row["status"] == "in_use" and status == "disabled":
                raise MailboxInUse("cannot disable a mailbox that is in use")
            await self.db.execute("UPDATE mailboxes SET status=? WHERE id=?", (status, mailbox_id))
        if notes is not None:
            await self.db.execute("UPDATE mailboxes SET notes=? WHERE id=?", (notes, mailbox_id))
        if clear_cooldown:
            await self.db.execute(
                "UPDATE mailboxes SET cooldown_until=NULL, health=CASE WHEN health='cooling' THEN 'unknown' ELSE health END, "
                "status=CASE WHEN status='cooling' THEN 'ready' ELSE status END WHERE id=?",
                (mailbox_id,),
            )
        return await self.get_mailbox(mailbox_id)

    async def check_mailbox(self, mailbox_id: str) -> dict:
        mailbox = await self.get_mailbox(mailbox_id)
        if mailbox["status"] == "in_use":
            raise MailboxInUse("cannot probe a mailbox that is in use")
        policy = (await self.get_risk_policy())["policy"]
        now = utc_now()
        try:
            secret = self.secrets.get(mailbox_id)
        except KeyError as exc:
            raise MailboxUnhealthy("mailbox secrets are missing") from exc
        try:
            probe_mailbox(mailbox["email"], secret["client_id"], secret["refresh_token"])
        except Exception as exc:
            classified = classify_mailbox_error(str(exc))
            fail_count = int(mailbox["fail_count"] or 0) + 1
            max_fail = int(policy["mailbox_max_fail"])
            next_status = next_mailbox_status(fail_count=fail_count, max_fail=max_fail, classified=classified)
            cooldown = plus_seconds(int(policy["mailbox_fail_cooldown_seconds"]), now=now)
            health = "dead" if next_status == "disabled" else "cooling"
            await self.db.execute(
                "UPDATE mailboxes SET health=?, fail_count=?, cooldown_until=?, last_checked_at=?, last_error=?, status=? WHERE id=?",
                (health, fail_count, cooldown, now, str(exc)[:300], next_status, mailbox_id),
            )
            await self.add_risk_event(
                kind="mailbox_check",
                severity="error",
                mailbox_id=mailbox_id,
                message=classified,
                detail={"error": str(exc)[:300], "fail_count": fail_count},
            )
            return await self.get_mailbox(mailbox_id)
        restore = "bound" if mailbox["bound_account_id"] else "ready"
        await self.db.execute(
            "UPDATE mailboxes SET health='ok', fail_count=0, cooldown_until=NULL, last_checked_at=?, last_ok_at=?, "
            "last_error=NULL, status=? WHERE id=?",
            (now, now, restore if mailbox["status"] in {"ready", "cooling", "bound"} else mailbox["status"], mailbox_id),
        )
        await self.add_risk_event(kind="mailbox_check", mailbox_id=mailbox_id, message="ok")
        return await self.get_mailbox(mailbox_id)

    async def check_mailboxes(self, limit: int = 20) -> list[dict]:
        rows = await self.db.fetchall(
            f"SELECT {MAILBOX_COLUMNS} FROM mailboxes WHERE status IN ('ready','cooling','bound') "
            "ORDER BY last_checked_at IS NOT NULL, last_checked_at LIMIT ?",
            (limit,),
        )
        return [await self.check_mailbox(row["id"]) for row in rows]

    def _mailbox_usable(self, mailbox: dict, policy: dict) -> None:
        if mailbox["status"] not in {"ready"}:
            raise MailboxInUse("mailbox is not ready")
        if mailbox["health"] in {"dead"}:
            raise MailboxUnhealthy("mailbox token is dead")
        if is_future(mailbox.get("cooldown_until")):
            raise RateLimited("mailbox is in cooldown")
        if mailbox["health"] == "cooling":
            raise RateLimited("mailbox is in cooldown")
        if int(mailbox.get("fail_count") or 0) >= int(policy["mailbox_max_fail"]):
            raise MailboxUnhealthy("mailbox exceeded fail budget")

    async def _enforce_register_cadence(self, policy: dict) -> None:
        interval = int(policy["register_min_interval_seconds"])
        last = await self.db.fetchone(
            "SELECT created_at FROM jobs WHERE type='register' ORDER BY created_at DESC LIMIT 1"
        )
        if last and interval > 0:
            last_at = datetime.fromisoformat(last["created_at"].replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta = (now - last_at).total_seconds()
            if delta < interval:
                raise RateLimited(f"register interval {interval}s not elapsed")
        cap = int(policy["register_daily_cap"])
        if cap > 0:
            day = utc_now()[:10]
            row = await self.db.fetchone(
                "SELECT COUNT(*) AS n FROM jobs WHERE type='register' AND created_at LIKE ?",
                (f"{day}%",),
            )
            if int(row["n"] if row else 0) >= cap:
                raise RateLimited("daily register cap reached")

    async def persist_lease(self, lease: StickyLease, *, job_id: str | None = None, state: str = "held") -> dict:
        lease_id = _id("ls")
        await self.db.execute(
            "INSERT INTO proxy_leases(id, sid, region, sticky_minutes, state, job_id, created_at, last_ip_hash) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (lease_id, lease.sid, lease.region, lease.sticky_minutes, state, job_id, utc_now(), None),
        )
        return await self.get_lease(lease_id)

    async def get_lease(self, lease_id: str) -> dict:
        row = await self.db.fetchone("SELECT * FROM proxy_leases WHERE id=?", (lease_id,))
        if not row:
            raise NotFound("lease not found")
        return row

    async def list_leases(self, state: str | None = None) -> list[dict]:
        if state:
            return await self.db.fetchall("SELECT * FROM proxy_leases WHERE state=? ORDER BY created_at DESC", (state,))
        return await self.db.fetchall("SELECT * FROM proxy_leases ORDER BY created_at DESC")

    async def release_lease(self, lease_id: str) -> dict:
        await self.get_lease(lease_id)
        await self.db.execute("UPDATE proxy_leases SET state='released' WHERE id=?", (lease_id,))
        return await self.get_lease(lease_id)

    async def rotate_lease(self, lease_id: str) -> dict:
        old = await self.get_lease(lease_id)
        await self.db.execute("UPDATE proxy_leases SET state='rotated' WHERE id=?", (lease_id,))
        fresh = self.cliproxy.build_lease(region=old["region"], sticky_minutes=old["sticky_minutes"])
        return await self.persist_lease(fresh, job_id=old["job_id"])

    async def enqueue_register(
        self,
        *,
        mailbox_id: str | None,
        region: str | None,
        backend: str | None,
        sticky_minutes: int | None,
    ) -> dict:
        if await self.is_paused():
            raise Paused("registration is paused")
        policy = (await self.get_risk_policy())["policy"]
        await self._enforce_register_cadence(policy)
        backend_name = backend or self.settings.default_backend
        if mailbox_id:
            mailbox = await self.get_mailbox(mailbox_id)
            self._mailbox_usable(mailbox, policy)
        else:
            mailbox = await self.db.fetchone(
                f"SELECT {MAILBOX_COLUMNS} FROM mailboxes WHERE status='ready' "
                "AND (cooldown_until IS NULL OR cooldown_until <= ?) "
                "AND health IN ('unknown','ok') ORDER BY imported_at LIMIT 1",
                (utc_now(),),
            )
            if not mailbox:
                raise AccountNotAvailable("no ready mailbox")
            mailbox_id = mailbox["id"]
            self._mailbox_usable(mailbox, policy)
        chosen_region = region or self.settings.default_region
        minutes = sticky_minutes or int(policy.get("sticky_minutes") or self.settings.sticky_minutes)
        lease = self.cliproxy.build_lease(region=chosen_region, sticky_minutes=minutes)
        stored = await self.persist_lease(lease)
        job_id = _id("job")
        snap = snapshot_for_job(policy, region=lease.region, sticky_minutes=lease.sticky_minutes, sid=lease.sid)
        await self.db.execute(
            "INSERT INTO jobs(id, type, mailbox_id, lease_id, backend, status, error_class, error_message, created_at, finished_at, risk_snapshot) "
            "VALUES(?,?,?,?,?,'queued',NULL,NULL,?,NULL,?)",
            (job_id, "register", mailbox_id, stored["id"], backend_name, utc_now(), json.dumps(snap, ensure_ascii=False)),
        )
        await self.db.execute("UPDATE mailboxes SET status='in_use' WHERE id=?", (mailbox_id,))
        await self.db.execute("UPDATE proxy_leases SET job_id=? WHERE id=?", (job_id, stored["id"]))
        await self.add_risk_event(
            kind="register_enqueue",
            mailbox_id=mailbox_id,
            job_id=job_id,
            message=f"region={lease.region}",
            detail=snap,
        )
        return await self.get_job(job_id)

    async def enqueue_batch(self, *, count: int, region: str | None, backend: str | None, sticky_minutes: int | None) -> list[dict]:
        jobs = []
        for _ in range(count):
            jobs.append(
                await self.enqueue_register(
                    mailbox_id=None,
                    region=region,
                    backend=backend,
                    sticky_minutes=sticky_minutes,
                )
            )
        return jobs

    async def enqueue_keepalive(self, account_id: str, *, backend: str | None = None) -> dict:
        if await self.is_paused():
            raise Paused("registration is paused")
        account = await self.get_account(account_id)
        if account["status"] not in {"registered", "leased"}:
            raise AccountNotAvailable("account cannot be kept")
        mailbox = await self.db.fetchone(
            f"SELECT {MAILBOX_COLUMNS} FROM mailboxes WHERE bound_account_id=? OR email=?",
            (account_id, account["email"]),
        )
        if not mailbox:
            raise AccountNotAvailable("account has no bound mailbox")
        if mailbox["status"] == "in_use":
            raise MailboxInUse("mailbox is in use")
        if mailbox["health"] == "dead":
            raise MailboxUnhealthy("mailbox token is dead")
        if is_future(mailbox.get("cooldown_until")):
            raise RateLimited("mailbox is in cooldown")
        policy = (await self.get_risk_policy())["policy"]
        region = account.get("region") or self.settings.default_region
        minutes = int(policy.get("sticky_minutes") or self.settings.sticky_minutes)
        lease = self.cliproxy.build_lease(region=region, sticky_minutes=minutes)
        stored = await self.persist_lease(lease)
        job_id = _id("job")
        snap = snapshot_for_job(policy, region=lease.region, sticky_minutes=lease.sticky_minutes, sid=lease.sid)
        backend_name = backend or self.settings.default_backend
        await self.db.execute(
            "INSERT INTO jobs(id, type, mailbox_id, lease_id, backend, status, error_class, error_message, created_at, finished_at, account_id, risk_snapshot) "
            "VALUES(?,?,?,?,?,'queued',NULL,NULL,?,NULL,?,?)",
            (job_id, "keepalive", mailbox["id"], stored["id"], backend_name, utc_now(), account_id, json.dumps(snap, ensure_ascii=False)),
        )
        await self.db.execute("UPDATE mailboxes SET status='in_use' WHERE id=?", (mailbox["id"],))
        await self.db.execute("UPDATE proxy_leases SET job_id=? WHERE id=?", (job_id, stored["id"]))
        await self.add_risk_event(
            kind="keepalive_enqueue",
            mailbox_id=mailbox["id"],
            account_id=account_id,
            job_id=job_id,
            message="keepalive queued",
            detail=snap,
        )
        return await self.get_job(job_id)

    async def enqueue_keepalive_due(self, limit: int = 10) -> list[dict]:
        now = utc_now()
        rows = await self.db.fetchall(
            "SELECT * FROM accounts WHERE status IN ('registered','leased') "
            "AND keepalive_due_at IS NOT NULL AND keepalive_due_at <= ? "
            "ORDER BY keepalive_due_at LIMIT ?",
            (now, limit),
        )
        jobs = []
        for row in rows:
            jobs.append(await self.enqueue_keepalive(row["id"]))
        return jobs

    async def list_jobs(self, status: str | None = None, backend: str | None = None, job_type: str | None = None) -> list[dict]:
        sql = "SELECT * FROM jobs WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if backend:
            sql += " AND backend=?"
            params.append(backend)
        if job_type:
            sql += " AND type=?"
            params.append(job_type)
        sql += " ORDER BY created_at DESC"
        rows = await self.db.fetchall(sql, tuple(params))
        return [await self._job_view(row) for row in rows]

    async def get_job(self, job_id: str) -> dict:
        row = await self.db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise NotFound("job not found")
        return await self._job_view(row)

    async def _job_view(self, row: dict) -> dict:
        mailbox = None
        if row["mailbox_id"]:
            mailbox = await self.db.fetchone("SELECT email FROM mailboxes WHERE id=?", (row["mailbox_id"],))
        lease = None
        if row["lease_id"]:
            lease = await self.db.fetchone("SELECT * FROM proxy_leases WHERE id=?", (row["lease_id"],))
        snap = row.get("risk_snapshot")
        if isinstance(snap, str) and snap:
            try:
                row["risk_snapshot"] = json.loads(snap)
            except json.JSONDecodeError:
                pass
        return {
            **row,
            "email": mailbox["email"] if mailbox else None,
            "lease": lease,
        }

    async def cancel_job(self, job_id: str) -> dict:
        job = await self.get_job(job_id)
        if job["status"] not in {"queued", "running"}:
            raise Conflict("job cannot be cancelled")
        await self.db.execute(
            "UPDATE jobs SET status='cancelled', finished_at=?, error_class='cancelled' WHERE id=?",
            (utc_now(), job_id),
        )
        await self._release_mailbox_after_job(job)
        if job["lease_id"]:
            await self.db.execute("UPDATE proxy_leases SET state='released' WHERE id=?", (job["lease_id"],))
        return await self.get_job(job_id)

    async def retry_job(self, job_id: str) -> dict:
        job = await self.get_job(job_id)
        if job["status"] not in {"failed", "blocked", "cancelled"}:
            raise Conflict("job cannot be retried")
        if job["type"] == "keepalive":
            if job["mailbox_id"]:
                await self.db.execute(
                    "UPDATE mailboxes SET status=CASE WHEN bound_account_id IS NULL THEN 'ready' ELSE 'bound' END WHERE id=?",
                    (job["mailbox_id"],),
                )
            return await self.enqueue_keepalive(job["account_id"], backend=job["backend"])
        if job["mailbox_id"]:
            await self.db.execute("UPDATE mailboxes SET status='ready' WHERE id=?", (job["mailbox_id"],))
        return await self.enqueue_register(
            mailbox_id=job["mailbox_id"],
            region=job["lease"]["region"] if job.get("lease") else None,
            backend=job["backend"],
            sticky_minutes=job["lease"]["sticky_minutes"] if job.get("lease") else None,
        )

    async def claim_next_job(self) -> dict | None:
        row = await self.db.fetchone(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        )
        if not row:
            return None
        await self.db.execute("UPDATE jobs SET status='running' WHERE id=?", (row["id"],))
        return await self.get_job(row["id"])

    async def _release_mailbox_after_job(self, job: dict, *, bind_account_id: str | None = None) -> None:
        if not job.get("mailbox_id"):
            return
        if bind_account_id:
            await self.db.execute(
                "UPDATE mailboxes SET status='bound', bound_account_id=?, health='ok', fail_count=0, "
                "cooldown_until=NULL, last_ok_at=? WHERE id=?",
                (bind_account_id, utc_now(), job["mailbox_id"]),
            )
            return
        mailbox = await self.get_mailbox(job["mailbox_id"])
        restore = "bound" if mailbox.get("bound_account_id") else "ready"
        await self.db.execute("UPDATE mailboxes SET status=? WHERE id=?", (restore, job["mailbox_id"]))

    async def finish_job_success(self, job_id: str, result) -> dict:
        job = await self.get_job(job_id)
        await self.db.execute(
            "UPDATE jobs SET status='succeeded', finished_at=? WHERE id=?",
            (utc_now(), job_id),
        )
        if job["lease_id"]:
            await self.db.execute("UPDATE proxy_leases SET state='released' WHERE id=?", (job["lease_id"],))
        policy = (await self.get_risk_policy())["policy"]
        region = None
        if job.get("lease"):
            region = job["lease"]["region"]
        if job["type"] == "keepalive":
            due = plus_hours(
                float(policy["keepalive_interval_hours"]),
                jitter_minutes=int(policy.get("keepalive_jitter_minutes") or 0),
            )
            await self.db.execute(
                "UPDATE accounts SET last_keepalive_at=?, keepalive_due_at=? WHERE id=?",
                (utc_now(), due, job["account_id"]),
            )
            await self._release_mailbox_after_job(job)
            await self.add_risk_event(
                kind="keepalive_ok",
                account_id=job["account_id"],
                mailbox_id=job["mailbox_id"],
                job_id=job_id,
                message="keepalive succeeded",
            )
            return await self.get_job(job_id)

        existing = await self.db.fetchone("SELECT id FROM accounts WHERE email=?", (result.email,))
        if existing:
            account_id = existing["id"]
            await self.db.execute(
                "UPDATE accounts SET status='registered', job_id=?, registered_at=?, region=? WHERE id=?",
                (job_id, result.registered_at, region, account_id),
            )
        else:
            account_id = _id("ac")
            due = plus_hours(
                float(policy["keepalive_interval_hours"]),
                jitter_minutes=int(policy.get("keepalive_jitter_minutes") or 0),
            )
            await self.db.execute(
                "INSERT INTO accounts(id, email, status, job_id, registered_at, console, leased_to, lease_expires_at, region, keepalive_due_at) "
                "VALUES(?,?,?,?,?,?,NULL,NULL,?,?)",
                (account_id, result.email, "registered", job_id, result.registered_at, result.console, region, due),
            )
        await self._release_mailbox_after_job(job, bind_account_id=account_id)
        await self.add_risk_event(
            kind="register_ok",
            account_id=account_id,
            mailbox_id=job["mailbox_id"],
            job_id=job_id,
            message="registered",
            detail={"region": region},
        )
        return await self.get_job(job_id)

    async def finish_job_failure(self, job_id: str, exc: Exception) -> dict:
        job = await self.get_job(job_id)
        code = getattr(exc, "code", "pool_error")
        status = "blocked" if isinstance(exc, RegisterBlocked) else "failed"
        await self.db.execute(
            "UPDATE jobs SET status=?, error_class=?, error_message=?, finished_at=? WHERE id=?",
            (status, code, str(exc), utc_now(), job_id),
        )
        policy = (await self.get_risk_policy())["policy"]
        if job["mailbox_id"]:
            mailbox = await self.get_mailbox(job["mailbox_id"])
            fail_count = int(mailbox["fail_count"] or 0) + 1
            classified = classify_mailbox_error(str(exc))
            next_status = next_mailbox_status(
                fail_count=fail_count,
                max_fail=int(policy["mailbox_max_fail"]),
                classified=classified,
            )
            cooldown = plus_seconds(int(policy["mailbox_fail_cooldown_seconds"]))
            if next_status == "disabled":
                mailbox_status = "disabled"
                health = "dead"
            elif mailbox.get("bound_account_id"):
                mailbox_status = "bound"
                health = "cooling"
            else:
                mailbox_status = "cooling"
                health = "cooling"
            await self.db.execute(
                "UPDATE mailboxes SET status=?, health=?, fail_count=?, cooldown_until=?, last_error=?, last_checked_at=? WHERE id=?",
                (mailbox_status, health, fail_count, cooldown, str(exc)[:300], utc_now(), job["mailbox_id"]),
            )
        if job["lease_id"]:
            await self.db.execute("UPDATE proxy_leases SET state='failed' WHERE id=?", (job["lease_id"],))
        await self.add_risk_event(
            kind=f"{job['type']}_fail",
            severity="error",
            mailbox_id=job.get("mailbox_id"),
            account_id=job.get("account_id"),
            job_id=job_id,
            message=code,
            detail={"error": str(exc)[:300]},
        )
        return await self.get_job(job_id)

    async def mailbox_secrets(self, mailbox_id: str) -> dict[str, str]:
        mailbox = await self.get_mailbox(mailbox_id)
        secret = self.secrets.get(mailbox_id)
        return {"email": mailbox["email"], **secret}

    async def list_accounts(self, status: str | None = None) -> list[dict]:
        if status:
            return await self.db.fetchall("SELECT * FROM accounts WHERE status=? ORDER BY registered_at DESC", (status,))
        return await self.db.fetchall("SELECT * FROM accounts ORDER BY registered_at DESC")

    async def get_account(self, account_id: str) -> dict:
        row = await self.db.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
        if not row:
            raise NotFound("account not found")
        return row

    async def stats(self) -> dict:
        async def count(table: str, status: str) -> int:
            row = await self.db.fetchone(f"SELECT COUNT(*) AS n FROM {table} WHERE status=?", (status,))
            return int(row["n"] if row else 0)

        due = await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM accounts WHERE keepalive_due_at IS NOT NULL AND keepalive_due_at <= ?",
            (utc_now(),),
        )
        mail = await self.mailbox_stats()
        return {
            "registered": await count("accounts", "registered"),
            "leased": await count("accounts", "leased"),
            "disabled": await count("accounts", "disabled"),
            "retired": await count("accounts", "retired"),
            "mailboxes_ready": mail["ready"],
            "mailboxes_bound": mail["bound"],
            "mailboxes_cooling": mail["cooling"],
            "jobs_queued": await count("jobs", "queued"),
            "jobs_running": await count("jobs", "running"),
            "keepalive_due": int(due["n"] if due else 0),
        }

    async def checkout(self, count: int, ttl_seconds: int, leased_to: str) -> list[dict]:
        policy = (await self.get_risk_policy())["policy"]
        available = await self.db.fetchall(
            "SELECT * FROM accounts WHERE status='registered' ORDER BY registered_at LIMIT ?",
            (count,),
        )
        if len(available) < count:
            raise AccountNotAvailable("not enough registered accounts")
        if policy.get("checkout_requires_healthy_mailbox"):
            for row in available:
                mailbox = await self.db.fetchone(
                    f"SELECT {MAILBOX_COLUMNS} FROM mailboxes WHERE bound_account_id=? OR email=?",
                    (row["id"], row["email"]),
                )
                if mailbox and mailbox["health"] in {"dead", "cooling"}:
                    raise MailboxUnhealthy(f"bound mailbox is {mailbox['health']}")
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        out = []
        for row in available:
            await self.db.execute(
                "UPDATE accounts SET status='leased', leased_to=?, lease_expires_at=? WHERE id=?",
                (leased_to, expires, row["id"]),
            )
            out.append(await self.get_account(row["id"]))
        return out

    async def release_account(self, account_id: str) -> dict:
        account = await self.get_account(account_id)
        if account["status"] != "leased":
            raise AccountNotAvailable("account is not leased")
        await self.db.execute(
            "UPDATE accounts SET status='registered', leased_to=NULL, lease_expires_at=NULL WHERE id=?",
            (account_id,),
        )
        return await self.get_account(account_id)

    async def disable_account(self, account_id: str) -> dict:
        await self.get_account(account_id)
        await self.db.execute(
            "UPDATE accounts SET status='disabled', leased_to=NULL, lease_expires_at=NULL WHERE id=?",
            (account_id,),
        )
        mailbox = await self.db.fetchone(
            f"SELECT {MAILBOX_COLUMNS} FROM mailboxes WHERE bound_account_id=?",
            (account_id,),
        )
        if mailbox and mailbox["status"] != "in_use":
            await self.db.execute("UPDATE mailboxes SET status='disabled' WHERE id=?", (mailbox["id"],))
        return await self.get_account(account_id)

    async def export_accounts(self, account_id: str | None = None) -> dict:
        if account_id:
            rows = [await self.get_account(account_id)]
        else:
            rows = await self.list_accounts()
        if not rows:
            raise AccountNotAvailable("no accounts to export")
        self.settings.exports_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for row in rows:
            payload = {
                "email": row["email"],
                "status": row["status"],
                "registered_at": row["registered_at"],
                "console": row["console"],
                "region": row.get("region"),
                "last_keepalive_at": row.get("last_keepalive_at"),
                "keepalive_due_at": row.get("keepalive_due_at"),
            }
            target = self.settings.exports_dir / f"account-{row['id']}.json"
            if target.suffix.lower() in {".env", ".ini"} or target.name.startswith(".env"):
                raise PoolError("registration result must be a JSON file, not an env file", code="export_forbidden")
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            paths.append(str(target))
        return {"paths": paths, "count": len(paths)}
