"""SQLite persistence."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS mailboxes (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  health TEXT NOT NULL DEFAULT 'unknown',
  fail_count INTEGER NOT NULL DEFAULT 0,
  cooldown_until TEXT,
  last_checked_at TEXT,
  last_ok_at TEXT,
  last_error TEXT,
  notes TEXT NOT NULL DEFAULT '',
  source TEXT,
  bound_account_id TEXT
);

CREATE TABLE IF NOT EXISTS proxy_leases (
  id TEXT PRIMARY KEY,
  sid TEXT NOT NULL,
  region TEXT NOT NULL,
  sticky_minutes INTEGER NOT NULL,
  state TEXT NOT NULL,
  job_id TEXT,
  created_at TEXT NOT NULL,
  last_ip_hash TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  mailbox_id TEXT,
  lease_id TEXT,
  backend TEXT NOT NULL,
  status TEXT NOT NULL,
  error_class TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  account_id TEXT,
  risk_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  job_id TEXT,
  registered_at TEXT NOT NULL,
  console TEXT NOT NULL,
  leased_to TEXT,
  lease_expires_at TEXT,
  region TEXT,
  last_keepalive_at TEXT,
  keepalive_due_at TEXT,
  risk_score INTEGER NOT NULL DEFAULT 0,
  keep_notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS controls (
  name TEXT PRIMARY KEY,
  paused INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_policy (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_events (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  severity TEXT NOT NULL,
  mailbox_id TEXT,
  account_id TEXT,
  job_id TEXT,
  message TEXT NOT NULL,
  detail TEXT
);
"""

COLUMN_MIGRATIONS = {
    "mailboxes": {
        "health": "TEXT NOT NULL DEFAULT 'unknown'",
        "fail_count": "INTEGER NOT NULL DEFAULT 0",
        "cooldown_until": "TEXT",
        "last_checked_at": "TEXT",
        "last_ok_at": "TEXT",
        "last_error": "TEXT",
        "notes": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT",
        "bound_account_id": "TEXT",
    },
    "jobs": {
        "account_id": "TEXT",
        "risk_snapshot": "TEXT",
    },
    "accounts": {
        "region": "TEXT",
        "last_keepalive_at": "TEXT",
        "keepalive_due_at": "TEXT",
        "risk_score": "INTEGER NOT NULL DEFAULT 0",
        "keep_notes": "TEXT NOT NULL DEFAULT ''",
    },
}


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._migrate_columns()
        await self._conn.commit()

    async def _migrate_columns(self) -> None:
        for table, columns in COLUMN_MIGRATIONS.items():
            cursor = await self.conn.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in await cursor.fetchall()}
            for name, ddl in columns.items():
                if name not in existing:
                    await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def execute(self, sql: str, params: tuple | dict = ()) -> aiosqlite.Cursor:
        cursor = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cursor

    async def fetchone(self, sql: str, params: tuple | dict = ()) -> dict | None:
        cursor = await self.conn.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        cursor = await self.conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
