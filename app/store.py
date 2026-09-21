"""Account pool store: mailboxes, jobs, leases, accounts."""

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
    NotFound,
    Paused,
    PoolError,
    RegisterBlocked,
)
from app.services.cliproxy import CliproxyManager, StickyLease
from app.services.mailbox import parse_mailbox_path, parse_mailbox_txt
from app.services.secrets import SecretStore


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

    async def health(self) -> dict:
        await self.ensure_controls()
        paused = await self.is_paused()
        return {
            "database": True,
            "cliproxy": self.settings.redacted_proxy(),
            "worker_running": self.worker_running,
            "paused": paused,
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

    async def import_mailboxes(self, *, path: str | None, text: str | None) -> list[dict]:
        if bool(path) == bool(text):
            raise MailboxInvalid("provide exactly one of path or text")
        try:
            records = parse_mailbox_path(Path(path)) if path else parse_mailbox_txt(text or "")
        except ValueError as exc:
            raise MailboxInvalid(str(exc)) from exc
        created: list[dict] = []
        for record in records:
            existing = await self.db.fetchone("SELECT id, status FROM mailboxes WHERE email=?", (record["email"],))
            if existing:
                if existing["status"] == "in_use":
                    raise MailboxInUse(f"mailbox already in use: {record['email']}")
                mailbox_id = existing["id"]
                await self.db.execute(
                    "UPDATE mailboxes SET status='ready', imported_at=? WHERE id=?",
                    (utc_now(), mailbox_id),
                )
            else:
                mailbox_id = _id("mb")
                await self.db.execute(
                    "INSERT INTO mailboxes(id, email, status, imported_at) VALUES(?,?,?,?)",
                    (mailbox_id, record["email"], "ready", utc_now()),
                )
            self.secrets.put(mailbox_id, record["client_id"], record["refresh_token"])
            created.append(await self.get_mailbox(mailbox_id))
        return created

    async def list_mailboxes(self, status: str | None = None) -> list[dict]:
        if status:
            return await self.db.fetchall(
                "SELECT id, email, status, imported_at FROM mailboxes WHERE status=? ORDER BY imported_at",
                (status,),
            )
        return await self.db.fetchall("SELECT id, email, status, imported_at FROM mailboxes ORDER BY imported_at")

    async def get_mailbox(self, mailbox_id: str) -> dict:
        row = await self.db.fetchone(
            "SELECT id, email, status, imported_at FROM mailboxes WHERE id=?",
            (mailbox_id,),
        )
        if not row:
            raise NotFound("mailbox not found")
        return row

    async def patch_mailbox(self, mailbox_id: str, status: str) -> dict:
        row = await self.get_mailbox(mailbox_id)
        if row["status"] == "in_use" and status == "disabled":
            raise MailboxInUse("cannot disable a mailbox that is in use")
        await self.db.execute("UPDATE mailboxes SET status=? WHERE id=?", (status, mailbox_id))
        return await self.get_mailbox(mailbox_id)

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
        backend_name = backend or self.settings.default_backend
        if mailbox_id:
            mailbox = await self.get_mailbox(mailbox_id)
            if mailbox["status"] != "ready":
                raise MailboxInUse("mailbox is not ready")
        else:
            mailbox = await self.db.fetchone(
                "SELECT id, email, status, imported_at FROM mailboxes WHERE status='ready' ORDER BY imported_at LIMIT 1"
            )
            if not mailbox:
                raise AccountNotAvailable("no ready mailbox")
            mailbox_id = mailbox["id"]
        lease = self.cliproxy.build_lease(region=region, sticky_minutes=sticky_minutes)
        stored = await self.persist_lease(lease)
        job_id = _id("job")
        await self.db.execute(
            "INSERT INTO jobs(id, type, mailbox_id, lease_id, backend, status, error_class, error_message, created_at, finished_at) "
            "VALUES(?,?,?,?,?,'queued',NULL,NULL,?,NULL)",
            (job_id, "register", mailbox_id, stored["id"], backend_name, utc_now()),
        )
        await self.db.execute("UPDATE mailboxes SET status='in_use' WHERE id=?", (mailbox_id,))
        await self.db.execute("UPDATE proxy_leases SET job_id=? WHERE id=?", (job_id, stored["id"]))
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

    async def list_jobs(self, status: str | None = None, backend: str | None = None) -> list[dict]:
        sql = "SELECT * FROM jobs WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if backend:
            sql += " AND backend=?"
            params.append(backend)
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
        if job["mailbox_id"]:
            await self.db.execute("UPDATE mailboxes SET status='ready' WHERE id=?", (job["mailbox_id"],))
        if job["lease_id"]:
            await self.db.execute("UPDATE proxy_leases SET state='released' WHERE id=?", (job["lease_id"],))
        return await self.get_job(job_id)

    async def retry_job(self, job_id: str) -> dict:
        job = await self.get_job(job_id)
        if job["status"] not in {"failed", "blocked", "cancelled"}:
            raise Conflict("job cannot be retried")
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

    async def finish_job_success(self, job_id: str, result) -> dict:
        job = await self.get_job(job_id)
        await self.db.execute(
            "UPDATE jobs SET status='succeeded', finished_at=? WHERE id=?",
            (utc_now(), job_id),
        )
        if job["mailbox_id"]:
            await self.db.execute("UPDATE mailboxes SET status='exhausted' WHERE id=?", (job["mailbox_id"],))
        if job["lease_id"]:
            await self.db.execute("UPDATE proxy_leases SET state='released' WHERE id=?", (job["lease_id"],))
        account_id = _id("ac")
        await self.db.execute(
            "INSERT INTO accounts(id, email, status, job_id, registered_at, console, leased_to, lease_expires_at) "
            "VALUES(?,?,?,?,?,?,NULL,NULL)",
            (account_id, result.email, "registered", job_id, result.registered_at, result.console),
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
        if job["mailbox_id"] and status != "blocked":
            await self.db.execute("UPDATE mailboxes SET status='ready' WHERE id=?", (job["mailbox_id"],))
        if job["mailbox_id"] and status == "blocked":
            await self.db.execute("UPDATE mailboxes SET status='ready' WHERE id=?", (job["mailbox_id"],))
        if job["lease_id"]:
            await self.db.execute("UPDATE proxy_leases SET state='failed' WHERE id=?", (job["lease_id"],))
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

        return {
            "registered": await count("accounts", "registered"),
            "leased": await count("accounts", "leased"),
            "disabled": await count("accounts", "disabled"),
            "retired": await count("accounts", "retired"),
            "mailboxes_ready": await count("mailboxes", "ready"),
            "jobs_queued": await count("jobs", "queued"),
            "jobs_running": await count("jobs", "running"),
        }

    async def checkout(self, count: int, ttl_seconds: int, leased_to: str) -> list[dict]:
        available = await self.db.fetchall(
            "SELECT * FROM accounts WHERE status='registered' ORDER BY registered_at LIMIT ?",
            (count,),
        )
        if len(available) < count:
            raise AccountNotAvailable("not enough registered accounts")
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
            }
            target = self.settings.exports_dir / f"account-{row['id']}.json"
            if target.suffix.lower() in {".env", ".ini"} or target.name.startswith(".env"):
                raise PoolError("registration result must be a JSON file, not an env file", code="export_forbidden")
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            paths.append(str(target))
        return {"paths": paths, "count": len(paths)}
