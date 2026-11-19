"""Regression tests for the findings of the security review.

One section per finding, numbered as in the review. Each test failed
against the code as reviewed.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from starlette.testclient import TestClient

from mo_admin.config import ConfigError, load_settings
from mo_admin.security import LoginThrottle

from .conftest import migration_files, temporary_schema
from .fakes import FakeProvider
from .helpers import LOCAL_PASSWORD, LOCAL_USER, add_key, audit_rows, csrf_token, post, sign_in, today
from .test_oidc import ROLE, complete

ROOT = Path(__file__).resolve().parents[1]
ENV = {"ADMIN_DATABASE_URL": "postgresql://u:p@localhost/db"}
FORM = {"name": "k", "owner": "o", "rate_limit_rpm": "60", "access": "all", "submission_id": "h1"}


def no_real_waiting(client: TestClient) -> list[float]:
    """Replace the slow-down's sleep with a recorder; returns the list of delays."""
    delays: list[float] = []
    slowdown = getattr(client.app.state.ctx, "slowdown", None)
    if slowdown is not None:
        async def record(seconds: float) -> None:
            delays.append(seconds)

        slowdown.sleep = record
    return delays


def attempt(client: TestClient, password: str, address: str | None = None, *, headers=None, username: str = LOCAL_USER):
    """One sign-in attempt from a fresh browser."""
    client.cookies.clear()
    if headers is None:
        headers = {"X-Forwarded-For": address} if address else {}
    return client.post(
        "/login", headers=headers,
        data={"csrf_token": csrf_token(client), "username": username, "password": password},
    )


def replay(make_client, cookies: dict[str, str], **overrides) -> TestClient:
    """A copied cookie presented to a restarted console (new process, same secret, same database)."""
    restarted = make_client(**overrides)
    for name, value in cookies.items():
        restarted.cookies.set(name, value)
    return restarted


def sso_client(make_client, provider: FakeProvider, **overrides) -> TestClient:
    settings = {
        "local_login": False, "oidc_issuer": provider.issuer, "oidc_client_id": provider.client_id,
        "oidc_client_secret": provider.client_secret, "oidc_required_role": ROLE,
    }
    settings.update(overrides)
    return make_client(**settings)


# =================================================================== finding 1
# The break-glass account could be locked for everyone by anyone.

def test_1_wrong_guesses_from_many_addresses_never_refuse_the_right_password(make_client, db):
    client = make_client()
    no_real_waiting(client)
    statuses = {attempt(client, f"guess-{i}", f"2001:db8::{i:x}").status_code for i in range(60)}
    assert statuses == {401}, "a wrong guess is wrong; it does not lock the form for everyone"
    assert attempt(client, LOCAL_PASSWORD, "198.51.100.7").status_code == 303


def test_1_many_failures_slow_every_attempt_down_but_let_the_right_password_in(make_client, db):
    client = make_client(login_max_attempts=3)
    delays = no_real_waiting(client)
    for i in range(40):
        attempt(client, "nope", f"203.0.113.{i}")
    assert delays, "past the threshold each attempt waits first"
    assert delays == sorted(delays) and delays[-1] > delays[0], "the wait grows with the failures"
    assert max(delays) <= 5.0, "and is capped"
    waited = len(delays)
    assert attempt(client, LOCAL_PASSWORD, "198.51.100.7").status_code == 303
    assert len(delays) == waited + 1, "the right password waited too, and then got in"


def test_1_slow_down_counts_recent_failures_only():
    from mo_admin.security import LoginSlowdown

    now = [0.0]
    slowdown = LoginSlowdown(threshold=3, step=0.5, max_delay=2.0, window=100.0, clock=lambda: now[0])
    assert slowdown.delay() == 0
    for _ in range(3):
        slowdown.record_failure()
    assert slowdown.delay() == 0.5
    slowdown.record_failure()
    slowdown.record_failure()
    assert slowdown.delay() == 1.5
    for _ in range(500):
        slowdown.record_failure()
    assert slowdown.delay() == 2.0
    now[0] = 101.0
    assert slowdown.delay() == 0, "failures older than the window no longer count"


def test_1_per_source_lock_still_applies(make_client, db):
    client = make_client(login_max_attempts=3)
    no_real_waiting(client)
    assert [attempt(client, "nope", "203.0.113.5").status_code for _ in range(3)] == [401, 401, 429]
    assert attempt(client, LOCAL_PASSWORD, "203.0.113.5").status_code == 429, "that address has to wait"
    assert attempt(client, LOCAL_PASSWORD, "203.0.113.6").status_code == 303, "nobody else does"


def test_1_throttle_key_is_the_last_forwarded_for_entry_across_header_lines(make_client, db):
    """HAProxy-style: the client's own X-Forwarded-For line comes first, the proxy adds a second line."""
    client = make_client(login_max_attempts=3)
    no_real_waiting(client)
    statuses = [
        attempt(client, "nope", headers=[("X-Forwarded-For", f"6.6.6.{i}"), ("X-Forwarded-For", "192.0.2.10")]).status_code
        for i in range(3)
    ]
    assert statuses == [401, 401, 429], "the entry our proxy added counts, whatever the client put in front"
    other = attempt(client, LOCAL_PASSWORD, headers=[("X-Forwarded-For", "6.6.6.1"), ("X-Forwarded-For", "192.0.2.77")])
    assert other.status_code == 303


def test_1_per_source_table_is_bounded():
    now = [0.0]
    throttle = LoginThrottle(clock=lambda: now[0])
    for index in range(30_000):
        throttle.failure(f"source-{index}")
        now[0] += 0.001
    assert len(throttle._state) <= 10_001


def test_1_a_weak_local_password_stops_the_start():
    with pytest.raises(ConfigError, match="ADMIN_LOCAL_PASSWORD"):
        load_settings({**ENV, "ADMIN_LOCAL_PASSWORD": "elevenchars"})
    assert load_settings({**ENV, "ADMIN_LOCAL_PASSWORD": "twelve-chars"}).local_enabled
    unused = load_settings({**ENV, "ADMIN_LOCAL_PASSWORD": "short", "ADMIN_LOCAL_LOGIN": "off"})
    assert not unused.local_enabled, "a password that is switched off is not checked"


# =================================================================== finding 2
# Sign-out did not survive a restart; sessions were not tied to the credential.

def test_2_sign_out_survives_a_restart(admin: TestClient, make_client, db):
    stolen = dict(admin.cookies)
    assert post(admin, "/logout").status_code == 303
    rows = db.execute("SELECT session_id, expires_at FROM admin_revoked_sessions").fetchall()
    assert len(rows) == 1 and rows[0]["expires_at"] > datetime.now(timezone.utc)

    restarted = replay(make_client, stolen)
    assert restarted.get("/keys").status_code == 303
    created = restarted.post("/keys/new", data={**FORM, "csrf_token": csrf_token(restarted)})
    assert created.status_code == 303 and created.headers["location"] == "/login"
    assert db.execute("SELECT count(*) AS n FROM api_keys").fetchone()["n"] == 0


def test_2_sign_out_reaches_another_running_process(admin: TestClient, make_client, db):
    other = replay(make_client, dict(admin.cookies))
    assert other.get("/keys").status_code == 200, "valid, and now remembered as valid for a few seconds"
    post(admin, "/logout")
    revocations = getattr(other.app.state.ctx, "revocations", None)
    if revocations is not None:
        revocations.forget()  # what a few seconds do
    assert other.get("/keys").status_code == 303


def test_2_a_restart_alone_does_not_sign_anyone_out(admin: TestClient, make_client, db):
    assert replay(make_client, dict(admin.cookies)).get("/keys").status_code == 200


def test_2_expired_revocations_are_purged(admin: TestClient, db):
    db.execute(
        "INSERT INTO admin_revoked_sessions (session_id, expires_at) VALUES "
        "('long-gone', now() - interval '1 hour'), ('still-relevant', now() + interval '1 hour')"
    )
    post(admin, "/logout")
    ids = {row["session_id"] for row in db.execute("SELECT session_id FROM admin_revoked_sessions")}
    assert "long-gone" not in ids and "still-relevant" in ids and len(ids) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"local_password": "a-brand-new-password-0001"},
        {"local_user": "renamed-admin"},
        {"local_login": False},
        {"local_password": ""},
    ],
    ids=["password rotated", "user renamed", "local login off", "password removed"],
)
def test_2_local_sessions_die_with_the_local_credential(admin: TestClient, make_client, db, change):
    restarted = replay(make_client, dict(admin.cookies), **change)
    assert restarted.get("/keys").status_code == 303


def test_2_oidc_sessions_die_when_single_sign_on_is_reconfigured(provider: FakeProvider, make_client, db):
    client = sso_client(make_client, provider)
    assert complete(client).status_code == 303
    cookies = dict(client.cookies)
    assert replay(make_client, cookies, **_sso(provider)).get("/keys").status_code == 200
    stricter = replay(make_client, cookies, **_sso(provider, oidc_required_role="another-role"))
    assert stricter.get("/keys").status_code == 303
    switched_off = replay(make_client, cookies)
    assert switched_off.get("/keys").status_code == 303


def _sso(provider: FakeProvider, **overrides) -> dict:
    values = {
        "local_login": False, "oidc_issuer": provider.issuer, "oidc_client_id": provider.client_id,
        "oidc_client_secret": provider.client_secret, "oidc_required_role": ROLE,
    }
    values.update(overrides)
    return values


def test_2_session_hours_are_capped():
    assert load_settings({**ENV, "ADMIN_SESSION_HOURS": "72"}).session_hours == 72
    with pytest.raises(ConfigError, match="ADMIN_SESSION_HOURS"):
        load_settings({**ENV, "ADMIN_SESSION_HOURS": "73"})


def test_2_a_missing_revoked_sessions_table_is_a_schema_problem(postgres_url, make_client):
    before_0005 = [path for path in migration_files() if path.name < "0005"]
    assert len(before_0005) == 4
    with temporary_schema(postgres_url, before_0005) as conninfo:
        client = make_client(database_url=conninfo)
        assert sign_in(client).status_code == 303
        page = client.get("/keys")
        assert page.status_code == 503 and "The database schema is not initialised" in page.text
        assert '<span class="tag">admin_revoked_sessions</span>' in page.text
        assert '<span class="tag">api_keys</span>' not in page.text
        assert post(client, "/logout").status_code == 303, "signing out still works"
        assert client.get("/keys").status_code == 303


# =================================================================== finding 3
# A far-future expiry bricked the console when the database is not in UTC.

def test_3_an_expiry_more_than_ten_years_ahead_is_a_form_error(admin: TestClient, db):
    refused = post(admin, "/keys/new", {**FORM, "expires_on": "9999-12-31"})
    assert refused.status_code == 422 and "10 years" in refused.text
    just_over = (today() + timedelta(days=3660)).isoformat()
    assert post(admin, "/keys/new", {**FORM, "expires_on": just_over}).status_code == 422
    assert db.execute("SELECT count(*) AS n FROM api_keys").fetchone()["n"] == 0

    within = (today() + timedelta(days=3640)).isoformat()
    assert post(admin, "/keys/new", {**FORM, "expires_on": within}).status_code == 201
    edited = post(admin, "/keys/1/edit", {**FORM, "expires_on": "9999-12-31"})
    assert edited.status_code == 422
    assert db.execute("SELECT expires_at FROM api_keys").fetchone()["expires_at"].date().isoformat() == within


def test_3_a_far_future_expiry_in_a_non_utc_database_does_not_break_the_pages(postgres_url, make_client):
    with temporary_schema(postgres_url, migration_files(), options="-c TimeZone=Europe/Berlin") as conninfo:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            assert conn.execute("SHOW TimeZone").fetchone()[0] == "Europe/Berlin"
            conn.execute(
                "INSERT INTO api_keys (key_hash, key_prefix, name, owner, expires_at) "
                "VALUES ('hash', 'sk-mo-aaaaaa', 'forever', 'o', '9999-12-31 23:59:59+00')"
            )
        client = make_client(database_url=conninfo)
        sign_in(client)
        for path in ("/", "/keys", "/keys/1", "/keys/1/edit", "/keys/1/delete", "/usage", "/models", "/audit"):
            assert client.get(path).status_code == 200, path
        assert post(client, "/keys/1/enabled", {"enabled": "0"}).status_code == 303

        # Such a key stays editable: its expiry may be left as it is, but not be set anew.
        form = {**FORM, "name": "forever", "rate_limit_rpm": "75"}
        assert post(client, "/keys/1/edit", {**form, "expires_on": "9999-12-31"}).status_code == 303
        assert post(client, "/keys/1/edit", {**form, "expires_on": "9999-12-30"}).status_code == 422
        with psycopg.connect(conninfo, autocommit=True) as conn:
            limit, expiry = conn.execute("SELECT rate_limit_rpm, expires_at::text FROM api_keys").fetchone()
        assert limit == 75 and expiry.startswith("10000-01-01"), "stored instant unchanged (shown in Berlin time)"
        assert post(client, "/keys/1/delete", {"confirm_name": "forever"}).status_code == 303


# =================================================================== finding 4
# Anonymous POST /logout grew the revocation list.

def test_4_anonymous_sign_out_revokes_nothing(client: TestClient, db):
    for _ in range(25):
        client.cookies.clear()
        assert post(client, "/logout").status_code == 303
    ctx = client.app.state.ctx
    held = len(getattr(ctx.sessions, "_revoked", {})) + len(getattr(getattr(ctx, "revocations", None), "_local", {}))
    assert held == 0
    assert db.execute("SELECT count(*) AS n FROM admin_revoked_sessions").fetchone()["n"] == 0


# =================================================================== finding 5
# Non-ASCII tokens crashed the comparison.

def test_5_a_non_ascii_csrf_token_is_just_a_wrong_token(client: TestClient):
    csrf_token(client)
    form = {"username": "a", "password": "b"}
    assert client.post("/login", data={**form, "csrf_token": "é"}).status_code == 403
    assert client.post("/login", data=form, headers={"X-CSRF-Token": b"\xe9"}).status_code == 403


def test_5_non_ascii_oidc_state_and_nonce_are_just_wrong(provider: FakeProvider, make_client, db):
    client = sso_client(make_client, provider)
    client.get("/auth/oidc/start")
    assert client.get("/auth/oidc/callback?code=x&state=%C3%A9").status_code == 400
    provider.fault = "unicode_nonce"
    assert complete(client).status_code == 400
    assert client.get("/keys").status_code == 303


# =================================================================== finding 6
# Signed-in requests that crashed with a 500.

@pytest.mark.parametrize(
    "path",
    [
        "/usage?to=0001-01-05",
        "/usage?from=9999-12-31&to=9999-12-31",
        "/usage.csv?to=0001-01-05",
        "/usage.csv?from=9999-12-31&to=9999-12-31",
        "/keys/%C2%B2",
        "/keys/%C2%B2/edit",
        "/keys/%C2%B2/delete",
        "/audit?before=%C2%B2",
        "/audit?before=abc",
        "/audit?actor=%00",
    ],
)
def test_6_odd_input_gets_an_error_page(admin: TestClient, db, path: str):
    add_key(db)
    response = admin.get(path)
    assert response.status_code in (400, 404), f"{path} -> {response.status_code}"
    assert f"Error {response.status_code}" in response.text, "a rendered page, not a bare failure"


def test_6_odd_form_input_is_refused_cleanly(admin: TestClient, db):
    add_key(db)
    assert post(admin, "/keys/%C2%B2/enabled", {"enabled": "0"}).status_code == 404
    assert post(admin, "/keys/%C2%B2/delete", {"confirm_name": "x"}).status_code == 404
    with_nul = post(admin, "/keys/new", {**FORM, "name": "bad\x00name", "notes": "a\x00b"})
    assert with_nul.status_code == 422 and "control characters" in with_nul.text
    assert db.execute("SELECT count(*) AS n FROM api_keys").fetchone()["n"] == 1


# =================================================================== finding 7
# OIDC edges.

def test_7_a_refused_account_is_audited_once_per_ten_minutes(provider: FakeProvider, make_client, db):
    provider.roles = ["viewer"]
    client = sso_client(make_client, provider)
    assert [complete(client).status_code for _ in range(6)] == [403] * 6

    def denied() -> list[dict]:
        return [row for row in audit_rows(db) if row["action"] == "auth.denied"]

    assert len(denied()) == 1

    provider.user = {**provider.user, "sub": "user-2002", "preferred_username": "tomas.reller"}
    complete(client)
    complete(client)
    assert [row["actor"] for row in denied()] == ["oidc:mira.castellan", "oidc:tomas.reller"]

    denials = getattr(client.app.state.ctx, "denials", None)
    if denials is not None:
        denials._clock = lambda: time.monotonic() + 601
    complete(client)
    assert len(denied()) == 3, "after ten minutes the same account is logged again"


def test_7_once_per_interval_helper():
    from mo_admin.security import OncePer

    now = [0.0]
    once = OncePer(600.0, clock=lambda: now[0], limit=3)
    assert [once.first("a"), once.first("a"), once.first("b")] == [True, False, True]
    now[0] = 599.0
    assert not once.first("a")
    now[0] = 600.5
    assert once.first("a")
    for key in ("c", "d", "e", "f"):
        once.first(key)
    assert len(once._seen) <= 3, "bounded, whatever is thrown at it"


@pytest.mark.parametrize("configured", ["without the slash", "with the slash"])
def test_7_an_issuer_that_ends_in_a_slash_works(provider: FakeProvider, make_client, db, configured: str):
    provider.issuer_suffix = "/"  # discovery and tokens say ".../"
    issuer = provider.issuer + ("/" if configured == "with the slash" else "")
    client = sso_client(make_client, provider, oidc_issuer=issuer)
    assert complete(client).status_code == 303
    assert client.get("/keys").status_code == 200


def test_7_issuer_from_the_environment_keeps_working_with_a_slash():
    settings = load_settings(
        {**ENV, "ADMIN_OIDC_ISSUER": "https://id.example.org/realms/x/", "ADMIN_OIDC_CLIENT_ID": "c",
         "ADMIN_OIDC_REQUIRED_ROLE": "r"}
    )
    assert settings.oidc_issuer.rstrip("/") == "https://id.example.org/realms/x"


def test_7_the_audit_actor_says_how_the_account_signed_in(provider: FakeProvider, make_client, db):
    """A local account and an OIDC user of the same name are different actors."""
    client = sso_client(make_client, provider, local_login=True, local_user="mira.castellan")
    assert sign_in(client, username="mira.castellan").status_code == 303
    assert post(client, "/keys/new", FORM).status_code == 201
    post(client, "/logout")
    assert complete(client).status_code == 303
    assert post(client, "/keys/1/enabled", {"enabled": "0"}).status_code == 303

    rows = audit_rows(db)
    assert [(row["actor"], row["action"]) for row in rows] == [
        ("local:mira.castellan", "auth.login"),
        ("local:mira.castellan", "key.create"),
        ("local:mira.castellan", "auth.logout"),
        ("oidc:mira.castellan", "auth.login"),
        ("oidc:mira.castellan", "key.disable"),
    ]
    assert rows[3]["detail"] == {"method": "oidc", "subject": "user-1001"}
    assert rows[4]["detail"]["subject"] == "user-1001", "the provider's stable id travels with every OIDC change"
    assert db.execute("SELECT created_by FROM api_keys").fetchone()["created_by"] == "local:mira.castellan"
    page = client.get("/audit").text
    assert "local account" in page and "single sign-on" in page


# =================================================================== finding 8
# Proxy and deployment.

def test_8_the_slash_redirect_does_not_downgrade_to_http(make_client, db):
    client = make_client(public_url="https://admin.example.org")
    behind_proxy = {"Host": "admin.example.org", "X-Forwarded-Proto": "https"}
    response = client.get("/keys/", headers=behind_proxy)
    assert response.status_code in (307, 308)
    assert response.headers["location"] == "/keys", "relative, so the browser stays on https"
    assert client.get("/keys/?status=expired", headers=behind_proxy).headers["location"] == "/keys?status=expired"
    assert client.get("/keys/1/", headers=behind_proxy).headers["location"] == "/keys/1"
    elsewhere = client.get("http://testserver//evil.example/")
    assert elsewhere.status_code == 404 and "location" not in elsewhere.headers


def test_8_start_warns_when_the_cookie_may_travel_without_secure(make_client, db, caplog):
    def warned() -> bool:
        return any("Secure" in r.getMessage() and "ADMIN_PUBLIC_URL" in r.getMessage() for r in caplog.records)

    with caplog.at_level(logging.WARNING):
        make_client(host="0.0.0.0")
        assert warned(), "reachable from other machines, and nothing says the address is https"
        caplog.clear()
        make_client(host="0.0.0.0", public_url="https://admin.example.org")
        assert not warned()
        make_client(host="127.0.0.1")
        make_client(host="::1")
        assert not warned()


def test_readme_states_what_the_review_asked_for():
    text = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "admin_revoked_sessions" in text, "where sign-outs are kept"
    assert "so a copied cookie stops working" not in text, "the old claim was too strong"
    assert "id_token_hint" in text, "sign-out at the provider is sent without it"
    assert "host network" in text.lower() and "ADMIN_HOST=127.0.0.1" in text


# ============================================================ seen in poc2.py
def test_body_guard_is_not_fooled_by_a_small_length_plus_chunked(client: TestClient):
    response = client.post(
        "/login", content=b"csrf_token=x",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Content-Length": "12",
                 "Transfer-Encoding": "chunked"},
    )
    assert response.status_code == 413
