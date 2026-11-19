"""Postgres for the suite: ``TEST_DATABASE_URL`` if set, else a throwaway Podman container.

Isolation is one database per router instance: :meth:`PostgresServer.create_database`
makes ``mo_e2e_<random>`` and :meth:`Database.drop` removes it again. With
``TEST_DATABASE_URL`` the role therefore needs ``CREATEDB`` (the default
``postgres`` superuser of a CI service container has it).
"""

from __future__ import annotations

import atexit
import datetime as dt
import hashlib
import os
import secrets
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from .util import HOST, free_port

LEGACY_SCHEMA = Path(__file__).with_name("legacy_schema.sql")
DEFAULT_IMAGE = "docker.io/library/postgres:17-alpine"


def _url(user: str, password: str | None, host: str, port: int | str, dbname: str) -> str:
    auth = quote(user, safe="")
    if password:
        auth += ":" + quote(password, safe="")
    return f"postgresql://{auth}@{host}:{port}/{quote(dbname, safe='')}"


def hash_key(token: str) -> str:
    """Unsalted SHA-256 hex of the full token: the format of ``api_keys.key_hash``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class ApiKey:
    id: int
    token: str
    name: str
    owner: str

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


class Database:
    """One test database plus the SQL helpers tests use to create keys and read usage."""

    def __init__(self, server: PostgresServer, name: str) -> None:
        self.server = server
        self.name = name
        self.url = server.url_for(name)
        self._conn: psycopg.Connection | None = None

    def url_via(self, port: int, host: str = HOST) -> str:
        """The same database reached through another address (a TCP relay)."""
        return _url(self.server.user, self.server.password, host, port, self.name)

    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row)
        return self._conn

    def execute(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        cur = self.conn.execute(query, params)
        return cur.fetchall() if cur.description else []

    def scalar(self, query: str, params: Any = None) -> Any:
        rows = self.execute(query, params)
        return next(iter(rows[0].values())) if rows else None

    def run_script(self, path: Path) -> None:
        """Run a multi-statement SQL file (simple query protocol, no parameters)."""
        self.conn.execute(path.read_text())

    def create_legacy_schema(self) -> None:
        self.run_script(LEGACY_SCHEMA)

    def tables(self) -> set[str]:
        rows = self.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {r["tablename"] for r in rows}

    def columns(self, table: str) -> set[str]:
        rows = self.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        )
        return {r["column_name"] for r in rows}

    # ---- api keys --------------------------------------------------------

    def create_key(
        self,
        *,
        rpm: int = 100_000,
        allowed: list[str] | None = None,
        enabled: bool = True,
        expires_at: dt.datetime | None = None,
        name: str | None = None,
        owner: str = "e2e",
        max_concurrency: int | None = None,
        token: str | None = None,
    ) -> ApiKey:
        """Insert a key the way the admin UI does and return it with its plaintext token."""
        token = token or "sk-mo-" + secrets.token_hex(20)
        name = name or f"key-{token[-6:]}"
        row = self.execute(
            """
            INSERT INTO api_keys (key_hash, key_prefix, name, owner, rate_limit_rpm,
                                  allowed_models, enabled, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (hash_key(token), token[:10], name, owner, rpm, allowed, enabled, expires_at),
        )[0]
        key = ApiKey(id=row["id"], token=token, name=name, owner=owner)
        if max_concurrency is not None:
            self.set_max_concurrency(key, max_concurrency)
        return key

    def set_max_concurrency(self, key: ApiKey, cap: int | None) -> None:
        # The column arrives with migration 0002. The legacy router never creates
        # it, so add it here: that keeps the C13 tests failing on an assertion
        # (no 429) instead of on a missing column.
        self.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS max_concurrency INTEGER")
        self.execute("UPDATE api_keys SET max_concurrency = %s WHERE id = %s", (cap, key.id))

    def update_key(self, key: ApiKey, **columns: Any) -> None:
        assignments = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(col)) for col in columns)
        query = sql.SQL("UPDATE api_keys SET {} WHERE id = %s").format(assignments)
        self.conn.execute(query, (*columns.values(), key.id))

    def delete_key(self, key: ApiKey) -> None:
        self.execute("DELETE FROM api_keys WHERE id = %s", (key.id,))

    # ---- usage -----------------------------------------------------------

    def usage_daily(self, key: ApiKey) -> dict[str, tuple[int, int]]:
        """``{route name: (request_count, error_count)}`` summed over days for one key."""
        rows = self.execute(
            "SELECT model, sum(request_count) AS r, sum(error_count) AS e FROM usage_daily "
            "WHERE api_key_id = %s GROUP BY model",
            (key.id,),
        )
        return {r["model"]: (int(r["r"]), int(r["e"])) for r in rows}

    def usage_hourly(self, key: ApiKey) -> list[dict[str, Any]]:
        return self.execute(
            "SELECT * FROM usage_hourly WHERE api_key_id = %s ORDER BY hour, model, route, outcome", (key.id,)
        )

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def drop(self) -> None:
        self.close()
        self.server.drop_database(self.name)


class PostgresServer:
    def __init__(self, admin_url: str, container: str | None = None) -> None:
        self.admin_url = admin_url
        self.container = container
        info = conninfo_to_dict(admin_url)
        self.host = str(info.get("host") or HOST)
        self.port = int(info.get("port") or 5432)
        self.user = str(info.get("user") or "postgres")
        self.password = info.get("password")
        self._databases: set[str] = set()

    # ---- construction ----------------------------------------------------

    @classmethod
    def from_environment(cls) -> PostgresServer:
        url = os.environ.get("TEST_DATABASE_URL")
        if url:
            server = cls(url)
            server.wait_ready(timeout=30)
            return server
        return cls.start_podman()

    @classmethod
    def start_podman(cls) -> PostgresServer:
        if shutil.which("podman") is None:
            raise RuntimeError(
                "no TEST_DATABASE_URL set and podman is not installed; cannot provide a Postgres"
            )
        name = f"mo-e2e-pg-{uuid.uuid4().hex[:10]}"
        port = free_port()
        password = secrets.token_hex(12)
        image = os.environ.get("TEST_POSTGRES_IMAGE", DEFAULT_IMAGE)
        cmd = [
            "podman", "run", "--detach", "--rm", "--name", name, "--label", "mo-e2e=1",
            "--env", f"POSTGRES_PASSWORD={password}",
            "--publish", f"{HOST}:{port}:5432",
            "--tmpfs", "/var/lib/postgresql/data",
            image,
            "-c", "fsync=off", "-c", "synchronous_commit=off", "-c", "full_page_writes=off",
            "-c", "max_connections=300",
        ]  # fmt: skip
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if done.returncode != 0:
            raise RuntimeError(f"podman could not start {image}: {done.stderr.strip()}")
        server = cls(_url("postgres", password, HOST, port, "postgres"), container=name)
        atexit.register(server.close)
        try:
            server.wait_ready(timeout=60)
        except Exception:
            logs = subprocess.run(["podman", "logs", "--tail", "40", name], capture_output=True, text=True)
            server.close()
            raise RuntimeError(
                f"throwaway Postgres did not become ready:\n{logs.stdout}{logs.stderr}"
            ) from None
        return server

    def wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                with psycopg.connect(self.admin_url, connect_timeout=2) as conn:
                    conn.execute("SELECT 1")
                return
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)

    # ---- databases -------------------------------------------------------

    def url_for(self, dbname: str) -> str:
        return _url(self.user, self.password, self.host, self.port, dbname)

    def create_database(self) -> Database:
        name = f"mo_e2e_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        self._databases.add(name)
        return Database(self, name)

    def drop_database(self, name: str) -> None:
        self._databases.discard(name)
        try:
            with psycopg.connect(self.admin_url, autocommit=True, connect_timeout=5) as conn:
                conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        except psycopg.Error:
            pass  # best effort; a throwaway server takes it with it anyway

    def version(self) -> int:
        with psycopg.connect(self.admin_url) as conn:
            return conn.info.server_version // 10000

    def close(self) -> None:
        for name in list(self._databases):
            self.drop_database(name)
        if self.container:
            container, self.container = self.container, None
            subprocess.run(
                ["podman", "rm", "--force", "--time", "0", container], capture_output=True, timeout=60
            )
