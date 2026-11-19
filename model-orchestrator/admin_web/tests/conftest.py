"""Test fixtures: a real Postgres, the application under a test client, and
the fake provider and router.

Postgres comes from ``TEST_DATABASE_URL`` when set. Otherwise a throwaway
``postgres:17-alpine`` container is started with rootless podman on a free
localhost port and removed afterwards. Either way the tests work in schemas
of their own (created for the run, dropped at the end), so a database that
already holds data is left alone.
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from starlette.testclient import TestClient

from mo_admin.config import Settings
from mo_admin.main import create_app

from .fakes import FakeProvider, FakeRouter, Served, free_port
from .helpers import LOCAL_PASSWORD, LOCAL_USER, sign_in

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
IMAGE = "docker.io/library/postgres:17-alpine"
TABLES = ("api_keys", "usage_daily", "usage_hourly", "admin_audit", "routes", "admin_revoked_sessions")


def _wait_for(url: str, seconds: float = 60.0) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            with psycopg.connect(url, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.3)


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    configured = os.environ.get("TEST_DATABASE_URL")
    if configured:
        yield configured
        return
    podman = shutil.which("podman")
    if podman is None:
        pytest.exit("Set TEST_DATABASE_URL or install podman: the tests need a real Postgres.", returncode=2)
    name = f"mo-admin-test-{uuid.uuid4().hex[:10]}"
    port = free_port()
    password = secrets.token_hex(12)
    subprocess.run(
        [
            podman, "run", "--detach", "--rm", "--pull=never", "--name", name,
            "--env", f"POSTGRES_PASSWORD={password}", "--env", "POSTGRES_DB=mo_admin_test",
            "--publish", f"127.0.0.1:{port}:5432", "--tmpfs", "/var/lib/postgresql/data", IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    try:
        url = f"postgresql://postgres:{password}@127.0.0.1:{port}/mo_admin_test"
        _wait_for(url)
        yield url
    finally:
        subprocess.run([podman, "rm", "--force", "--time", "0", name], capture_output=True)


def migration_files() -> list[Path]:
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert files, f"no migration files in {MIGRATIONS}"
    return files


@contextmanager
def temporary_schema(postgres_url: str, migrations: list[Path], options: str = "") -> Iterator[str]:
    """A fresh schema with the given migrations applied; yields a conninfo
    whose search_path is that schema. ``options`` adds session settings such
    as ``-c TimeZone=Europe/Berlin``."""
    schema = f"mo_test_{uuid.uuid4().hex[:12]}"
    conninfo = make_conninfo(postgres_url, options=f"-c search_path={schema} {options}".strip())
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            for path in migrations:  # in file-name order, as the router applies them
                conn.execute(path.read_text(encoding="utf-8"))
        yield conninfo
    finally:
        with psycopg.connect(postgres_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture(scope="session")
def database_url(postgres_url: str) -> Iterator[str]:
    with temporary_schema(postgres_url, migration_files()) as conninfo:
        yield conninfo


@pytest.fixture()
def db(database_url: str) -> Iterator[psycopg.Connection]:
    """A plain connection for arranging and asserting; tables start empty."""
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
        yield conn


@pytest.fixture()
def make_settings(database_url: str) -> Callable[..., Settings]:
    def build(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "database_url": database_url,
            "database_timeout": 2.0,
            "session_secret": "test-session-secret-" + "x" * 32,
            "local_login": True,
            "local_user": LOCAL_USER,
            "local_password": LOCAL_PASSWORD,
        }
        values.update(overrides)
        return Settings(**values)

    return build


@pytest.fixture()
def make_client(make_settings: Callable[..., Settings]) -> Iterator[Callable[..., TestClient]]:
    """Build test clients; each runs the app's lifespan and is closed at teardown."""
    opened: list[TestClient] = []

    def build(settings: Settings | None = None, **overrides: Any) -> TestClient:
        app = create_app(settings or make_settings(**overrides))
        client = TestClient(app, follow_redirects=False)
        client.__enter__()
        opened.append(client)
        return client

    yield build
    for client in reversed(opened):
        client.__exit__(None, None, None)


@pytest.fixture()
def client(db: psycopg.Connection, make_client: Callable[..., TestClient]) -> TestClient:
    """An anonymous client against an empty database."""
    return make_client()


@pytest.fixture()
def admin(client: TestClient) -> TestClient:
    """A client signed in with the local account."""
    sign_in(client)
    return client


@pytest.fixture()
def provider() -> Iterator[FakeProvider]:
    fake = FakeProvider()
    served = Served(fake.app).start()
    fake.issuer = served.url
    yield fake
    served.stop()


@pytest.fixture()
def serve_router() -> Iterator[Callable[[FakeRouter], str]]:
    running: list[Served] = []

    def start(fake: FakeRouter) -> str:
        served = Served(fake.app).start()
        running.append(served)
        return served.url

    yield start
    for served in running:
        served.stop()
