"""Shared helpers for the tests: signing in, CSRF tokens, arranging rows."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg
from starlette.testclient import TestClient

LOCAL_USER = "ops"
LOCAL_PASSWORD = "correct-horse-battery-staple"
LOCAL_ACTOR = "local:" + LOCAL_USER  # how the local account appears in the audit log

_CSRF_HEADER = re.compile(r'"X-CSRF-Token": "([^"]+)"')


def csrf_token(client: TestClient) -> str:
    """The CSRF token of the client's current session.

    Every HTML page carries it, signed in or not, including the 404 page,
    which needs neither a database nor a sign-in.
    """
    response = client.get("/no-such-page-for-the-token")
    match = _CSRF_HEADER.search(response.text)
    assert match, f"no CSRF token on the page (status {response.status_code})"
    return match.group(1)


def sign_in(client: TestClient, username: str = LOCAL_USER, password: str = LOCAL_PASSWORD, **form: str):
    return client.post(
        "/login", data={"csrf_token": csrf_token(client), "username": username, "password": password, **form}
    )


def post(client: TestClient, path: str, data: dict[str, Any] | None = None, **kwargs: Any):
    """POST a form with the session's CSRF token."""
    return client.post(path, data={"csrf_token": csrf_token(client), **(data or {})}, **kwargs)


def today() -> date:
    return datetime.now(timezone.utc).date()


def add_route(db: psycopg.Connection, name: str, kind: str = "chat") -> None:
    db.execute("INSERT INTO routes (name, type) VALUES (%s, %s)", (name, kind))


def add_key(db: psycopg.Connection, name: str = "test-key", owner: str = "Owner One", **columns: Any) -> int:
    values: dict[str, Any] = {
        "key_hash": hashlib.sha256(f"seed-{name}".encode()).hexdigest(),
        "key_prefix": "sk-mo-" + hashlib.sha256(name.encode()).hexdigest()[:6],
        "name": name,
        "owner": owner,
        "rate_limit_rpm": 100,
    }
    values.update(columns)
    names = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    row = db.execute(f"INSERT INTO api_keys ({names}) VALUES ({marks}) RETURNING id", list(values.values())).fetchone()
    return row["id"]


def add_daily(
    db: psycopg.Connection, key_id: int, route: str, requests: int, errors: int = 0,
    *, days_ago: int = 0, owner: str = "Owner One",
) -> None:
    db.execute(
        "INSERT INTO usage_daily (day, api_key_id, owner, model, request_count, error_count) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (today() - timedelta(days=days_ago), key_id, owner, route, requests, errors),
    )


def add_hourly(
    db: psycopg.Connection, key_id: int, route: str, model: str, outcome: str, requests: int,
    *, prompt: int = 0, completion: int = 0, duration_ms: int = 0, days_ago: int = 0, hour: int | None = None,
    owner: str = "Owner One",
) -> None:
    """One usage_hourly row.

    ``days_ago=0`` means the current hour, which is always today (UTC) and
    always inside "the last 24 hours". For earlier days the hour defaults to
    00:00, which is always outside the last 24 hours. Tests built on these
    two anchors do not depend on the time of day they run at.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if days_ago == 0 and hour is None:
        stamp = now
    else:
        stamp = datetime.combine(now.date() - timedelta(days=days_ago), datetime.min.time(), tzinfo=timezone.utc)
        stamp += timedelta(hours=hour or 0)
    db.execute(
        "INSERT INTO usage_hourly (hour, api_key_id, owner, model, route, outcome, requests, "
        "prompt_tokens, completion_tokens, duration_ms_sum) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (stamp, key_id, owner, model, route, outcome, requests, prompt, completion, duration_ms),
    )


def read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def audit_rows(db: psycopg.Connection) -> list[dict[str, Any]]:
    return db.execute("SELECT actor, action, target, detail FROM admin_audit ORDER BY id").fetchall()
