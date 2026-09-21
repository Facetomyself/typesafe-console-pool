"""FastAPI entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import uvicorn

from app import __version__
from app.api.v1 import router
from app.config import Settings
from app.db import Database
from app.errors import PoolError
from app.services.cliproxy import CliproxyManager
from app.services.queue import JobQueue
from app.services.secrets import SecretStore
from app.store import PoolStore


def create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI:
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.exports_dir.mkdir(parents=True, exist_ok=True)
        settings.profiles_dir.mkdir(parents=True, exist_ok=True)
        database = Database(settings.database_path)
        await database.connect()
        secrets = SecretStore(settings.secrets_path)
        cliproxy = CliproxyManager(settings)
        pool = PoolStore(settings, database, secrets, cliproxy)
        await pool.ensure_controls()
        queue = JobQueue(settings, pool)
        app.state.settings = settings
        app.state.database = database
        app.state.store = pool
        app.state.queue = queue
        if start_worker:
            await queue.start()
        try:
            yield
        finally:
            if start_worker:
                await queue.stop()
            await database.close()

    app = FastAPI(
        title="TypeSafe Console Pool",
        version=__version__,
        description=(
            "Account pool for TypeSafe Console email-OTP registration. "
            "Mailbox input is a four-field Outlook txt. Registration results "
            "are stored as JSON rows, never .env files. Registration traffic "
            "uses Cliproxy sticky leases."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.exception_handler(PoolError)
    async def pool_error_handler(_request: Request, exc: PoolError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "data": None, "error": {"code": exc.code, "message": str(exc)}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "data": None,
                "error": {"code": "validation_error", "message": "request validation failed"},
            },
        )

    app.include_router(router)
    return app


app = create_app()


def run() -> None:
    settings = Settings.load()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
