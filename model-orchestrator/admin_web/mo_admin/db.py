"""Postgres access: one async pool, dict rows, and a schema check.

The website never creates or alters tables. The router owns the schema and
applies the migrations; here we only verify that what we need exists and
show a clear page when it does not.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

# Every column the website reads or writes. Checked once per process (and
# again on each request for as long as something is missing).
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "routes": ("name", "type", "updated_at"),
    "api_keys": (
        "id", "key_hash", "key_prefix", "name", "owner", "rate_limit_rpm",
        "allowed_models", "enabled", "expires_at", "created_at", "last_used_at",
        "max_concurrency", "owner_email", "created_by", "notes",
    ),
    "usage_daily": ("day", "api_key_id", "owner", "model", "request_count", "error_count"),
    "usage_hourly": (
        "hour", "api_key_id", "owner", "model", "route", "outcome", "requests",
        "prompt_tokens", "completion_tokens", "duration_ms_sum",
    ),
    "admin_audit": ("id", "at", "actor", "action", "target", "detail"),
    "admin_revoked_sessions": ("session_id", "expires_at"),
}


class SchemaNotReady(Exception):
    """Tables or columns the website needs are missing."""

    def __init__(self, missing: list[str]):
        super().__init__("database schema is not initialised")
        self.missing = missing


async def _configure(conn: psycopg.AsyncConnection) -> None:
    # Connections are autocommit, so no transaction is left open here.
    #
    # UTC, whatever the server or the connection string says: every date on
    # the pages is UTC, and a timestamp near the end of year 9999 rendered in
    # a zone east of Greenwich becomes year 10000, which Python cannot hold.
    await conn.execute("SET TIME ZONE 'UTC'")
    await conn.execute("SET statement_timeout = 15000")


class Database:
    def __init__(self, conninfo: str, *, max_size: int = 6, timeout: float = 5.0):
        self._pool = AsyncConnectionPool(
            conninfo,
            min_size=1,
            max_size=max_size,
            open=False,
            timeout=timeout,
            name="mo-admin",
            kwargs={"row_factory": dict_row, "autocommit": True},
            configure=_configure,
            check=AsyncConnectionPool.check_connection,
        )
        self._schema_ok = False

    async def open(self) -> None:
        # wait=False: the website starts even while Postgres is still down and
        # shows its "database unreachable" page until the pool connects.
        await self._pool.open(wait=False)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def connection(self, timeout: float | None = None) -> AsyncIterator[psycopg.AsyncConnection]:
        async with self._pool.connection(timeout=timeout) as conn:
            yield conn

    async def fetch_all(self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> list[dict]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    async def fetch_one(
        self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> dict | None:
        async with self._pool.connection(timeout=timeout) as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def missing_schema(self) -> list[str]:
        """Names of missing tables (``table``) and columns (``table.column``)."""
        rows = await self.fetch_all(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = ANY (current_schemas(false))
              AND table_name = ANY (%s)
            """,
            (list(REQUIRED_COLUMNS),),
        )
        present: dict[str, set[str]] = {}
        for row in rows:
            present.setdefault(row["table_name"], set()).add(row["column_name"])
        missing: list[str] = []
        for table, columns in REQUIRED_COLUMNS.items():
            if table not in present:
                missing.append(table)
                continue
            missing.extend(f"{table}.{c}" for c in columns if c not in present[table])
        return missing

    async def require_schema(self) -> None:
        if self._schema_ok:
            return
        missing = await self.missing_schema()
        if missing:
            raise SchemaNotReady(missing)
        self._schema_ok = True

    def forget_schema(self) -> None:
        self._schema_ok = False
