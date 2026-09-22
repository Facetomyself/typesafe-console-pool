"""Background registration worker."""

from __future__ import annotations

import asyncio
import logging

from app.backends.automation import AutomationBackend
from app.backends.protocol import ProtocolBackend
from app.config import Settings
from app.store import PoolStore

log = logging.getLogger("typesafe-console-pool.queue")


class JobQueue:
    def __init__(self, settings: Settings, store: PoolStore):
        self.settings = settings
        self.store = store
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.backends = {
            "protocol": ProtocolBackend(settings),
            "automation": AutomationBackend(settings),
        }

    async def start(self) -> None:
        self._stop.clear()
        self.store.worker_running = True
        self._task = asyncio.create_task(self._loop(), name="register-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.store.worker_running = False

    async def _loop(self) -> None:
        while not self._stop.is_set():
            job = await self.store.claim_next_job()
            if job is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                continue
            await self._run_job(job)

    async def _run_job(self, job: dict) -> None:
        backend = self.backends.get(job["backend"])
        if backend is None:
            await self.store.finish_job_failure(job["id"], RuntimeError(f"unknown backend {job['backend']}"))
            return
        mailbox = await self.store.mailbox_secrets(job["mailbox_id"])
        lease_row = job.get("lease") or {}
        lease = self.store.cliproxy.rebuild_lease(
            sid=lease_row["sid"],
            region=lease_row["region"],
            sticky_minutes=lease_row["sticky_minutes"],
        )
        try:
            if job["type"] == "keepalive":
                result = await asyncio.to_thread(
                    backend.keepalive, mailbox, lease.url, job["id"], job["account_id"]
                )
            else:
                result = await asyncio.to_thread(backend.register, mailbox, lease.url, job["id"])
            await self.store.finish_job_success(job["id"], result)
        except Exception as exc:
            log.exception("job %s failed", job["id"])
            await self.store.finish_job_failure(job["id"], exc)
