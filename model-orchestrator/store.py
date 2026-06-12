"""
Postgres-backed API key store + usage recording for the model orchestrator.

The router requires ROUTER_DATABASE_URL (postgresql://user:pass@host:port/db)
and refuses to start without it — API keys live exclusively in Postgres, the
YAML config only describes server + routes.

  - Keys are stored as SHA-256 hashes (`key_hash`); the plaintext key exists
    only in the moment the admin UI generates it.
  - Every key carries its own requests-per-minute limit (`rate_limit_rpm`),
    an optional allowlist of route names (`allowed_models`, NULL = all), an
    optional expiry, and an enabled flag.
  - An empty key table means DENY ALL requests, not anonymous access — the
    first key is expected to be created through the admin UI and is picked
    up by the refresh poll within ROUTER_KEYS_REFRESH_SECONDS (default 15).
  - Per-request usage is aggregated into `usage_daily` (one row per
    day × key × route) by a background writer fed from an in-memory queue,
    so the request path never waits on Postgres. Rows intentionally have no
    foreign key on api_keys: deleting a key keeps its usage history.
  - A DB outage at runtime degrades gracefully: the last known key set stays
    in memory and usage writes are dropped with a warning. A DB outage at
    startup fails the process (systemd restarts it until Postgres is up).
"""
import os
import asyncio
import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("model-orchestrator.store")

DATABASE_URL = os.environ.get("ROUTER_DATABASE_URL")
KEYS_REFRESH_SECONDS = float(os.environ.get("ROUTER_KEYS_REFRESH_SECONDS", "15"))

enabled = bool(DATABASE_URL)

# Statements are executed one by one — psycopg's extended query protocol does
# not allow multiple commands in a single execute().
_SCHEMA: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS routes (
        name        TEXT PRIMARY KEY,
        type        TEXT NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_keys (
        id              SERIAL PRIMARY KEY,
        key_hash        TEXT NOT NULL UNIQUE,
        key_prefix      TEXT NOT NULL,
        name            TEXT NOT NULL,
        owner           TEXT NOT NULL,
        rate_limit_rpm  INTEGER NOT NULL DEFAULT 100,
        allowed_models  TEXT[],
        enabled         BOOLEAN NOT NULL DEFAULT TRUE,
        expires_at      TIMESTAMPTZ,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_used_at    TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS usage_daily (
        day             DATE NOT NULL,
        api_key_id      INTEGER NOT NULL,
        owner           TEXT NOT NULL,
        model           TEXT NOT NULL,
        request_count   BIGINT NOT NULL DEFAULT 0,
        error_count     BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (day, api_key_id, model)
    )
    """,
]

_pool: Optional[AsyncConnectionPool] = None
_keys_by_hash: Dict[str, Dict[str, Any]] = {}
_usage_queue: "asyncio.Queue[Tuple[int, str, str, int]]" = asyncio.Queue(maxsize=10000)
_last_used_written: Dict[int, float] = {}
_tasks: List[asyncio.Task] = []
_last_refresh_at: Optional[float] = None  # monotonic time of last successful key load
_LAST_USED_MIN_INTERVAL = 60.0  # seconds between last_used_at writes per key


def hash_key(token: str) -> str:
    """SHA-256 hex of a full API key. Must match the admin UI's hashing."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def lookup(token: str) -> Optional[Dict[str, Any]]:
    """Return the cached metadata dict for a key token, or None if unknown."""
    return _keys_by_hash.get(hash_key(token))


def key_count() -> int:
    return len(_keys_by_hash)


async def init(routes: List[Tuple[str, str]]) -> None:
    """Open the pool, ensure the schema, load keys, start the workers.

    ``routes`` is the configured (name, type) list, mirrored into the
    `routes` table so the admin UI can offer an authoritative model-route
    picker without parsing the YAML.
    """
    global _pool
    _pool = AsyncConnectionPool(
        conninfo=DATABASE_URL,
        min_size=1,
        max_size=4,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=False,
    )
    # wait=True blocks until min_size connections exist — covers Postgres
    # still booting next to us; systemd restarts us if it takes longer.
    await _pool.open(wait=True, timeout=60.0)

    async with _pool.connection() as conn:
        for stmt in _SCHEMA:
            await conn.execute(stmt)
        await _sync_routes(conn, routes)

    await _load_keys()
    if not _keys_by_hash:
        logger.warning(
            "key store: api_keys table is empty — ALL requests will be rejected "
            "until a key is created (e.g. via the admin UI)"
        )

    _tasks.append(asyncio.create_task(_refresh_loop()))
    _tasks.append(asyncio.create_task(_usage_writer_loop()))
    logger.info(
        f"key store: {key_count()} key(s) loaded from Postgres, "
        f"refresh every {KEYS_REFRESH_SECONDS:.0f}s"
    )


async def close() -> None:
    for task in _tasks:
        task.cancel()
    for task in _tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _tasks.clear()
    if _pool is not None:
        await _pool.close()


async def _sync_routes(conn, routes: List[Tuple[str, str]]) -> None:
    """Mirror configured route names/types into the `routes` table."""
    if not routes:
        return
    names = [name for name, _ in routes]
    await conn.execute("DELETE FROM routes WHERE name <> ALL(%s)", (names,))
    for name, type_ in routes:
        await conn.execute(
            """
            INSERT INTO routes (name, type, updated_at) VALUES (%s, %s, now())
            ON CONFLICT (name) DO UPDATE SET type = EXCLUDED.type, updated_at = now()
            """,
            (name, type_),
        )


async def _load_keys() -> None:
    """Replace the in-memory key cache from the api_keys table."""
    global _keys_by_hash, _last_refresh_at
    async with _pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, key_hash, key_prefix, name, owner, rate_limit_rpm,
                   allowed_models, enabled, expires_at
            FROM api_keys
            """
        )
        rows = await cur.fetchall()
    new_cache: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        allowed = r["allowed_models"]
        new_cache[r["key_hash"]] = {
            "id": r["id"],
            "key_prefix": r["key_prefix"],
            "name": r["name"],
            "owner": r["owner"],
            "rate_limit_rpm": r["rate_limit_rpm"],
            # empty array is treated like NULL: no restriction
            "allowed_models": set(allowed) if allowed else None,
            "enabled": r["enabled"],
            "expires_at": r["expires_at"],
        }
    if len(new_cache) != len(_keys_by_hash):
        logger.info(f"key store: key set changed, {len(new_cache)} key(s) active")
    _keys_by_hash = new_cache
    _last_refresh_at = time.monotonic()


async def _refresh_loop() -> None:
    while True:
        await asyncio.sleep(KEYS_REFRESH_SECONDS)
        try:
            await _load_keys()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"key store: refresh failed, keeping last known keys: {e}")


async def health(timeout: float = 2.0) -> Dict[str, Any]:
    """Ping Postgres and report key-store state, for the /health endpoint.

    Never raises. ``status`` is ``healthy`` or ``unavailable`` — the router
    keeps authenticating from the cached key set either way, so an
    unavailable database means *degraded* (usage writes dropped, key
    changes frozen), not down. ``seconds_since_key_refresh`` growing far
    beyond ROUTER_KEYS_REFRESH_SECONDS is the staleness signal.
    """
    info: Dict[str, Any] = {
        "keys_loaded": len(_keys_by_hash),
        "seconds_since_key_refresh": (
            round(time.monotonic() - _last_refresh_at, 1)
            if _last_refresh_at is not None
            else None
        ),
        "usage_queue_depth": _usage_queue.qsize(),
    }

    async def _ping() -> None:
        async with _pool.connection(timeout=timeout) as conn:
            await conn.execute("SELECT 1")

    try:
        # wait_for also bounds a stale pooled connection that hangs on
        # execute — pool.connection()'s timeout only covers checkout.
        await asyncio.wait_for(_ping(), timeout=timeout + 1.0)
        info["status"] = "healthy"
    except Exception as e:
        info["status"] = "unavailable"
        info["error"] = f"{type(e).__name__}: {e}"[:200]
    return info


def record_usage(token: Optional[str], route_name: str, is_error: bool) -> None:
    """Queue one request for the daily usage aggregate. Never blocks."""
    if not token:
        return
    meta = _keys_by_hash.get(hash_key(token))
    if meta is None:
        return
    try:
        _usage_queue.put_nowait((meta["id"], meta["owner"], route_name, 1 if is_error else 0))
    except asyncio.QueueFull:
        logger.warning("key store: usage queue full, dropping usage record")


async def _usage_writer_loop() -> None:
    """Drain the usage queue and upsert daily aggregates.

    Batches whatever is queued at wake-up into one upsert per
    (key, route) pair. last_used_at is written at most once per key per
    _LAST_USED_MIN_INTERVAL to keep the api_keys table cold.
    """
    while True:
        item = await _usage_queue.get()
        batch = [item]
        while True:
            try:
                batch.append(_usage_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        counts: Dict[Tuple[int, str, str], List[int]] = {}
        for key_id, owner, route_name, err in batch:
            agg = counts.setdefault((key_id, owner, route_name), [0, 0])
            agg[0] += 1
            agg[1] += err

        now = time.monotonic()
        touch_ids = sorted(
            {
                key_id
                for key_id, _, _ in counts
                if now - _last_used_written.get(key_id, 0.0) >= _LAST_USED_MIN_INTERVAL
            }
        )

        try:
            async with _pool.connection() as conn:
                for (key_id, owner, route_name), (requests, errors) in counts.items():
                    await conn.execute(
                        """
                        INSERT INTO usage_daily
                            (day, api_key_id, owner, model, request_count, error_count)
                        VALUES (CURRENT_DATE, %s, %s, %s, %s, %s)
                        ON CONFLICT (day, api_key_id, model) DO UPDATE
                        SET request_count = usage_daily.request_count + EXCLUDED.request_count,
                            error_count   = usage_daily.error_count + EXCLUDED.error_count,
                            owner         = EXCLUDED.owner
                        """,
                        (key_id, owner, route_name, requests, errors),
                    )
                if touch_ids:
                    await conn.execute(
                        "UPDATE api_keys SET last_used_at = now() WHERE id = ANY(%s)",
                        (touch_ids,),
                    )
                    for key_id in touch_ids:
                        _last_used_written[key_id] = now
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"key store: dropping {len(batch)} usage record(s), write failed: {e}")
