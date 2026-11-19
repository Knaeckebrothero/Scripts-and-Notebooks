"""Signed-out sessions, kept in Postgres.

The session cookie is self-contained, so signing out can only be enforced
by remembering which session ids must no longer be accepted. That memory
lives in ``admin_revoked_sessions`` (migration 0005): it survives restarts
and is shared by every process that serves the console.

Each signed-in request asks ``is_revoked``. The answer is cached per session
id for a few seconds, so a busy page costs one indexed lookup every few
seconds, and a sign-out elsewhere takes effect within that time. In the
process that handled the sign-out it takes effect at once.

When the database cannot answer, the check lets the request through: every
page that shows or changes anything needs the same database and fails with
its own clear error, and signing in with the local account has to keep
working during an outage.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

import psycopg
from psycopg_pool import PoolTimeout

from .db import Database

logger = logging.getLogger("mo_admin.sessions")

CACHE_SECONDS = 5.0
LOOKUP_TIMEOUT = 1.0


class Revocations:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.monotonic):
        self._db = db
        self._clock = clock
        self._local: dict[str, int] = {}                 # revoked by this process: id -> expiry (epoch)
        self._checked: dict[str, tuple[float, bool]] = {}  # id -> (when asked, answer)

    def forget(self) -> None:
        """Drop the cached answers (the next request asks the database again)."""
        self._checked.clear()

    async def is_revoked(self, session_id: str) -> bool:
        if session_id in self._local:
            return True
        now = self._clock()
        cached = self._checked.get(session_id)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            row = await self._db.fetch_one(
                "SELECT 1 AS hit FROM admin_revoked_sessions WHERE session_id = %s AND expires_at > now()",
                (session_id,),
                timeout=LOOKUP_TIMEOUT,
            )
        except (psycopg.Error, PoolTimeout) as exc:
            logger.debug("revocation lookup failed, letting the request through: %s", type(exc).__name__)
            revoked = False
        else:
            revoked = row is not None
        if len(self._checked) > 2000:
            self._checked = {k: v for k, v in self._checked.items() if now - v[0] < CACHE_SECONDS}
        self._checked[session_id] = (now, revoked)
        return revoked

    async def revoke(self, session_id: str, expires: int) -> None:
        """Refuse this session id from now until it would have expired."""
        wall = int(time.time())
        for old in [sid for sid, exp in self._local.items() if exp <= wall]:
            del self._local[old]
        self._local[session_id] = expires
        self._checked.pop(session_id, None)
        try:
            async with self._db.connection() as conn, conn.transaction():
                await conn.execute(
                    "INSERT INTO admin_revoked_sessions (session_id, expires_at) VALUES (%s, to_timestamp(%s)) "
                    "ON CONFLICT (session_id) DO NOTHING",
                    (session_id, expires),
                )
                # Rows past their expiry are sessions that ended anyway.
                await conn.execute("DELETE FROM admin_revoked_sessions WHERE expires_at <= now()")
        except (psycopg.Error, PoolTimeout) as exc:
            logger.warning(
                "sign-out could not be recorded in the database (%s); the session is refused by this "
                "process only until the database is back",
                type(exc).__name__,
            )
