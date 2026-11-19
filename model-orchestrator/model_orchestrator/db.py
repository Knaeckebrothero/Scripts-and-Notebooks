"""Postgres: connection pool, migrations, key loading, route sync, usage writes.

The database must be there at startup (the supervisor restarts the router until
it is). If it goes away later the router keeps serving from the cached key set;
``Database.healthy`` then turns false and usage records are retried and dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .auth import KeyRecord, key_from_row
from .usage import UsageBatch

logger = logging.getLogger(__name__)

# Session-level advisory lock taken while migrations run, so two routers
# starting against one database do not apply them twice.
MIGRATION_LOCK_ID = 7_305_808_869_231_650
_MIGRATION_FILE = re.compile(r"^(\d+)_([A-Za-z0-9_.-]+)\.sql$")

_OPEN_TIMEOUT_S = 60.0
_CHECKOUT_TIMEOUT_S = 5.0
_STATEMENT_TIMEOUT_S = 15.0
_KEY_LOAD_TIMEOUT_S = 8.0

Row = dict[str, Any]
Pool = AsyncConnectionPool[AsyncConnection[Row]]


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path


def discover_migrations(directory: str | Path) -> list[Migration]:
    """The numbered SQL files of a directory, in order. Raises ``MigrationError``."""
    root = Path(directory)
    if not root.is_dir():
        raise MigrationError(f"migrations directory not found: {root}")
    found: dict[int, Migration] = {}
    for path in sorted(root.iterdir()):
        if path.suffix != ".sql" or not path.is_file():
            continue
        match = _MIGRATION_FILE.match(path.name)
        if match is None:
            raise MigrationError(f"migration file name must look like 0001_name.sql: {path.name}")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(
                f"two migration files share version {version}: {found[version].path.name} and {path.name}"
            )
        found[version] = Migration(version=version, name=match.group(2), path=path)
    return [found[version] for version in sorted(found)]


def pending_migrations(available: list[Migration], applied: set[int]) -> list[Migration]:
    return [m for m in available if m.version not in applied]


class Database:
    def __init__(self, dsn: str, *, migrations_dir: str | Path) -> None:
        self._dsn = dsn
        self._migrations_dir = Path(migrations_dir)
        self._pool: Pool | None = None
        self.healthy = False
        self.last_error: str | None = None
        self.last_ok_monotonic: float | None = None

    # -- lifecycle ----------------------------------------------------------

    async def open(self, *, timeout_s: float = _OPEN_TIMEOUT_S) -> None:
        pool = cast(
            Pool,
            AsyncConnectionPool(
                conninfo=self._dsn,
                min_size=1,
                max_size=4,
                kwargs={"autocommit": True, "row_factory": dict_row},
                open=False,
            ),
        )
        # wait=True blocks until a connection exists, which covers a Postgres
        # that is still starting next to us.
        try:
            await pool.open(wait=True, timeout=timeout_s)
        except BaseException:
            with contextlib.suppress(Exception):
                await pool.close()
            raise
        self._pool = pool
        self._mark_ok()

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()

    def _require_pool(self) -> Pool:
        if self._pool is None:
            raise RuntimeError("database is not open")
        return self._pool

    def _mark_ok(self) -> None:
        if not self.healthy and self.last_error is not None:
            logger.info("database: reachable again")
        self.healthy = True
        self.last_error = None
        self.last_ok_monotonic = time.monotonic()

    def _mark_failed(self, exc: BaseException) -> None:
        self.healthy = False
        self.last_error = f"{type(exc).__name__}: {' '.join(str(exc).split())}"[:200]

    @contextlib.asynccontextmanager
    async def _tracked(self, timeout_s: float = _STATEMENT_TIMEOUT_S) -> AsyncIterator[AsyncConnection[Row]]:
        """A pooled connection; the outcome of the block updates ``healthy``."""
        pool = self._require_pool()
        try:
            async with asyncio.timeout(timeout_s):
                async with pool.connection(timeout=_CHECKOUT_TIMEOUT_S) as conn:
                    yield conn
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._mark_failed(exc)
            raise
        else:
            self._mark_ok()

    # -- migrations -----------------------------------------------------------

    async def migrate(self) -> list[Migration]:
        """Apply the migrations that are not recorded yet. Returns those applied now.

        Each file is sent as one simple query (no parameters), so it may hold
        several statements; the file and its ``schema_migrations`` row are one
        transaction. Files must not contain transaction control of their own.
        """
        available = discover_migrations(self._migrations_dir)
        done: list[Migration] = []
        async with self._tracked(timeout_s=300.0) as conn:
            await conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
            try:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version     INTEGER PRIMARY KEY,
                        name        TEXT NOT NULL,
                        applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cursor = await conn.execute("SELECT version FROM schema_migrations")
                applied = {int(row["version"]) for row in await cursor.fetchall()}
                known = {m.version for m in available}
                ahead = sorted(applied - known)
                if ahead:
                    logger.warning(
                        "database has migration(s) %s that this build does not know; "
                        "it was migrated by a newer version",
                        ahead,
                    )
                for migration in pending_migrations(available, applied):
                    sql = migration.path.read_bytes()
                    async with conn.transaction():
                        await conn.execute(sql)
                        await conn.execute(
                            "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                            (migration.version, migration.name),
                        )
                    logger.info("database: applied migration %04d_%s", migration.version, migration.name)
                    done.append(migration)
            finally:
                with contextlib.suppress(Exception):
                    await conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))
        return done

    # -- routes -----------------------------------------------------------------

    async def sync_routes(self, routes: Mapping[str, str], *, prune: bool) -> None:
        """Mirror route names and types into ``routes`` (the admin UI's route picker).

        With ``prune`` every other row is removed, as before. Without it rows are
        only added or updated.
        """
        if not routes:
            return
        async with self._tracked() as conn:
            async with conn.transaction():
                if prune:
                    await conn.execute("DELETE FROM routes WHERE name <> ALL(%s)", (list(routes),))
                async with conn.cursor() as cursor:
                    await cursor.executemany(
                        """
                        INSERT INTO routes (name, type, updated_at) VALUES (%s, %s, now())
                        ON CONFLICT (name) DO UPDATE SET type = EXCLUDED.type, updated_at = now()
                        """,
                        list(routes.items()),
                    )

    # -- keys ---------------------------------------------------------------------

    async def load_keys(self) -> dict[str, KeyRecord]:
        async with self._tracked(timeout_s=_KEY_LOAD_TIMEOUT_S) as conn:
            cursor = await conn.execute(
                """
                SELECT id, key_hash, key_prefix, name, owner, rate_limit_rpm,
                       allowed_models, enabled, expires_at, max_concurrency
                FROM api_keys
                """
            )
            rows = await cursor.fetchall()
        return dict(key_from_row(row) for row in rows)

    # -- usage ----------------------------------------------------------------------

    async def write_usage(self, batch: UsageBatch, touch_key_ids: list[int]) -> None:
        """Upsert one batch in a single transaction (so a retry cannot count twice)."""
        daily = [
            (key_id, row.owner, route, row.requests, row.errors)
            for (key_id, route), row in batch.daily.items()
        ]
        hourly = [
            (
                hour,
                key_id,
                row.owner,
                model,
                route,
                outcome,
                row.requests,
                row.prompt_tokens,
                row.completion_tokens,
                row.duration_ms,
            )
            for (hour, key_id, model, route, outcome), row in batch.hourly.items()
        ]
        async with self._tracked() as conn:
            async with conn.transaction(), conn.cursor() as cursor:
                if daily:
                    await cursor.executemany(
                        """
                        INSERT INTO usage_daily
                            (day, api_key_id, owner, model, request_count, error_count)
                        VALUES (CURRENT_DATE, %s, %s, %s, %s, %s)
                        ON CONFLICT (day, api_key_id, model) DO UPDATE
                        SET request_count = usage_daily.request_count + EXCLUDED.request_count,
                            error_count   = usage_daily.error_count + EXCLUDED.error_count,
                            owner         = EXCLUDED.owner
                        """,
                        daily,
                    )
                if hourly:
                    await cursor.executemany(
                        """
                        INSERT INTO usage_hourly
                            (hour, api_key_id, owner, model, route, outcome,
                             requests, prompt_tokens, completion_tokens, duration_ms_sum)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (hour, api_key_id, model, route, outcome) DO UPDATE
                        SET requests          = usage_hourly.requests + EXCLUDED.requests,
                            prompt_tokens     = usage_hourly.prompt_tokens + EXCLUDED.prompt_tokens,
                            completion_tokens = usage_hourly.completion_tokens + EXCLUDED.completion_tokens,
                            duration_ms_sum   = usage_hourly.duration_ms_sum + EXCLUDED.duration_ms_sum,
                            owner             = EXCLUDED.owner
                        """,
                        hourly,
                    )
                if touch_key_ids:
                    await cursor.execute(
                        "UPDATE api_keys SET last_used_at = now() WHERE id = ANY(%s)", (touch_key_ids,)
                    )

    # -- health -----------------------------------------------------------------------

    async def ping(self, *, timeout_s: float = 2.0) -> bool:
        """Ask the database for a row. Never raises; updates ``healthy``."""
        try:
            async with self._tracked(timeout_s=timeout_s + 1.0) as conn:
                await conn.execute("SELECT 1")
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return True
