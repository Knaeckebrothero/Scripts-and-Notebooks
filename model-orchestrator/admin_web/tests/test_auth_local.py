"""Sign-in is required everywhere; the local account, its throttle, the
session cookie and sign-out."""
from __future__ import annotations

import re
import time

import pytest
from starlette.testclient import TestClient

from mo_admin.config import ConfigError, load_settings
from mo_admin.security import LoginThrottle, safe_next
from mo_admin.sessions import Session, SessionManager
from mo_admin.views import auth, keys, overview, system
from mo_admin.views import usage as usage_views

from .helpers import LOCAL_ACTOR, LOCAL_PASSWORD, LOCAL_USER, add_key, audit_rows, csrf_token, post, sign_in

PROTECTED = [overview.router, keys.router, usage_views.router, system.router]
PUBLIC_PATHS = {"/livez", "/login", "/logout", "/auth/oidc/start", "/auth/oidc/callback"}


def protected_routes() -> list[tuple[str, str]]:
    found = []
    for router in PROTECTED:
        for route in router.routes:
            for method in sorted(route.methods - {"HEAD"}):
                found.append((method, route.path.replace("{key_id}", "1")))
    return found


def test_every_route_is_either_protected_or_deliberately_public():
    """A new router must be added to PROTECTED (or PUBLIC_PATHS) on purpose."""
    protected = {path for _, path in protected_routes()}
    assert {r.path for r in auth.router.routes} | {r.path for r in system.public.routes} == PUBLIC_PATHS
    assert not protected & PUBLIC_PATHS
    assert {"/", "/keys", "/keys/new", "/keys/1", "/usage", "/usage.csv", "/models", "/audit"} <= protected
    for router in PROTECTED:
        assert any(d.dependency.__name__ == "require_user" for d in router.dependencies)


@pytest.mark.parametrize("method,path", protected_routes())
def test_anonymous_requests_are_sent_to_sign_in(client: TestClient, db, method: str, path: str):
    key_id = add_key(db)
    assert key_id == 1
    if method == "GET":
        response = client.get(path)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")
    else:
        # A valid CSRF token alone is not enough: there is no signed-in user.
        response = client.post(path, data={"csrf_token": csrf_token(client), "enabled": "0", "confirm_name": "test-key"})
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    assert "test-key" not in response.text
    row = db.execute("SELECT enabled FROM api_keys WHERE id = 1").fetchone()
    assert row == {"enabled": True}, "an anonymous request must not change anything"
    assert audit_rows(db) == []


def test_sign_in_redirect_remembers_where_you_were_going(client: TestClient):
    response = client.get("/usage?group=owner&from=2026-01-01")
    assert response.headers["location"] == "/login?next=%2Fusage%3Fgroup%3Downer%26from%3D2026-01-01"
    page = client.get(response.headers["location"])
    assert 'name="next" value="/usage?group=owner&amp;from=2026-01-01"' in page.text


def test_htmx_requests_get_401_and_a_redirect_header(client: TestClient):
    response = client.get("/keys", headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/keys?q=rag"})
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login?next=%2Fkeys%3Fq%3Drag"


def test_public_pages_need_no_session(client: TestClient):
    assert client.get("/livez").text == "ok"
    assert client.get("/login").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200
    assert "set-cookie" not in client.get("/livez").headers


def test_local_sign_in_and_out(admin: TestClient, db):
    page = admin.get("/")
    assert page.status_code == 200
    assert LOCAL_USER in page.text
    assert [(r["actor"], r["action"], r["detail"]) for r in audit_rows(db)] == [
        (LOCAL_ACTOR, "auth.login", {"method": "local"})
    ]

    cookie = admin.cookies.get("mo_admin_session")
    response = post(admin, "/logout")
    assert response.status_code == 303 and response.headers["location"] == "/login?signed_out=1"
    assert admin.get("/").status_code == 303
    assert audit_rows(db)[-1]["action"] == "auth.logout"

    # The old cookie is dead on the server, not just forgotten by the browser.
    admin.cookies.set("mo_admin_session", cookie)
    assert admin.get("/").status_code == 303


def test_session_cookie_attributes(client: TestClient):
    response = sign_in(client)
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("mo_admin_session=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie, "plain http: a Secure cookie would never come back"
    assert "strict-transport-security" not in response.headers


def test_cookie_is_secure_behind_an_https_proxy(client: TestClient):
    https = {"X-Forwarded-Proto": "https"}
    page = client.get("/no-such-page", headers=https)
    token = re.search(r'"X-CSRF-Token": "([^"]+)"', page.text).group(1)
    client.cookies.set("__Host-mo_admin_session", page.cookies["__Host-mo_admin_session"])
    response = client.post(
        "/login", headers=https,
        data={"csrf_token": token, "username": LOCAL_USER, "password": LOCAL_PASSWORD},
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-mo_admin_session=")
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_proxy_headers_can_be_distrusted(make_client):
    client = make_client(trust_proxy_headers=False)
    page = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert "Secure" not in page.headers["set-cookie"]


def test_wrong_password_is_refused(client: TestClient, db):
    response = sign_in(client, password="wrong")
    assert response.status_code == 401
    assert "The user name or password is wrong." in response.text
    assert client.get("/").status_code == 303
    response = sign_in(client, username="someone-else")
    assert response.status_code == 401
    assert audit_rows(db) == []


@pytest.mark.parametrize(
    "target,expected",
    [
        ("/keys?status=expired", "/keys?status=expired"),
        ("//evil.example/x", "/"),
        ("https://evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
        ("", "/"),
    ],
)
def test_next_only_accepts_local_paths(client: TestClient, target: str, expected: str):
    assert safe_next(target) == expected
    response = sign_in(client, next=target)
    assert response.headers["location"] == expected


def test_throttle_after_failed_attempts(make_client, db):
    client = make_client(login_max_attempts=3, login_lockout_seconds=30)
    throttle = client.app.state.ctx.throttle
    clock = [1000.0]
    throttle._clock = lambda: clock[0]

    assert sign_in(client, password="bad-1").status_code == 401
    assert sign_in(client, password="bad-2").status_code == 401
    locked = sign_in(client, password="bad-3")
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "30"
    assert "Too many failed attempts" in locked.text

    # While locked, even the right password is turned away.
    still = sign_in(client)
    assert still.status_code == 429
    assert client.get("/").status_code == 303

    clock[0] += 31
    assert sign_in(client).status_code == 303
    assert client.get("/").status_code == 200


def test_throttle_doubles_and_resets():
    now = [0.0]
    throttle = LoginThrottle(max_attempts=2, lockout=10, max_lockout=25, window=100, clock=lambda: now[0])
    assert throttle.failure("a") == 0
    assert throttle.failure("a") == 10
    assert throttle.retry_after("a") == 10
    assert throttle.retry_after("b") == 0, "other sources are not affected"
    now[0] = 11
    throttle.failure("a")
    assert throttle.failure("a") == 20, "the second lock is twice as long"
    now[0] = 40
    throttle.failure("a")
    assert throttle.failure("a") == 25, "capped at max_lockout"
    now[0] = 70
    throttle.success("a")
    assert throttle.failure("a") == 0, "a success starts from zero"


def test_one_persons_typos_do_not_lock_everyone_out(make_client):
    client = make_client(login_max_attempts=2)
    for _ in range(2):
        client.post(
            "/login", headers={"X-Forwarded-For": "203.0.113.5"},
            data={"csrf_token": csrf_token(client), "username": LOCAL_USER, "password": "typo"},
        )
    other = client.post(
        "/login", headers={"X-Forwarded-For": "203.0.113.6"},
        data={"csrf_token": csrf_token(client), "username": LOCAL_USER, "password": LOCAL_PASSWORD},
    )
    assert other.status_code == 303, "another address still gets in"


def test_local_sign_in_can_be_switched_off(make_client):
    client = make_client(local_login=False)
    page = client.get("/login")
    assert 'action="/login"' not in page.text
    assert "No sign-in method is configured." in page.text
    assert sign_in(client).status_code == 404
    assert client.get("/").status_code == 303


def test_local_sign_in_needs_a_password(make_client):
    client = make_client(local_password="")
    assert sign_in(client, password="").status_code == 404


# ------------------------------------------------------------------ sessions
def manager(**kwargs) -> SessionManager:
    return SessionManager("k" * 40, lifetime_seconds=kwargs.get("lifetime", 3600), idle_seconds=kwargs.get("idle", 600))


def signed_in(m: SessionManager) -> str:
    session = Session()
    m.sign_in(session, user="ops", name=None, email=None, method="local")
    return m.dump(session)


def test_session_round_trip_and_tampering():
    m = manager()
    cookie = signed_in(m)
    assert m.load(cookie).user == "ops"
    assert m.load(cookie[:-3] + "abc").user is None
    assert m.load("garbage").user is None
    assert manager().load(cookie).user == "ops"
    assert SessionManager("z" * 40, lifetime_seconds=3600, idle_seconds=600).load(cookie).user is None


def test_session_expires_absolutely_and_when_idle(monkeypatch):
    m = manager(lifetime=3600, idle=600)
    cookie = signed_in(m)
    start = time.time()
    monkeypatch.setattr(time, "time", lambda: start + 599)
    assert m.load(cookie).user == "ops"
    monkeypatch.setattr(time, "time", lambda: start + 601)
    assert m.load(cookie).user is None, "idle for longer than the idle timeout"

    # Activity refreshes the idle timer, but never past the absolute end.
    monkeypatch.setattr(time, "time", lambda: start + 500)
    refreshed = m.load(cookie)
    assert refreshed.dirty
    cookie = m.dump(refreshed)
    monkeypatch.setattr(time, "time", lambda: start + 1000)
    assert m.load(cookie).user == "ops"
    monkeypatch.setattr(time, "time", lambda: start + 3601)
    assert m.load(cookie).user is None


def test_background_polling_does_not_keep_a_session_alive(monkeypatch):
    m = manager(lifetime=3600, idle=600)
    cookie = signed_in(m)
    start = time.time()
    monkeypatch.setattr(time, "time", lambda: start + 500)
    assert not m.load(cookie, passive=True).dirty


def test_sign_in_starts_a_new_session(client: TestClient):
    before = csrf_token(client)
    sign_in(client)
    assert csrf_token(client) != before, "the CSRF token and session id are replaced at sign-in"


# -------------------------------------------------------------------- config
BASE = {"ADMIN_DATABASE_URL": "postgresql://u:p@localhost/db"}


def test_configuration_errors_name_the_variable():
    with pytest.raises(ConfigError, match="ADMIN_DATABASE_URL"):
        load_settings({})
    with pytest.raises(ConfigError, match="ADMIN_SESSION_SECRET"):
        load_settings({**BASE, "ADMIN_SESSION_SECRET": "short"})
    with pytest.raises(ConfigError, match="ADMIN_LOCAL_LOGIN"):
        load_settings({**BASE, "ADMIN_LOCAL_LOGIN": "maybe"})
    with pytest.raises(ConfigError, match="ADMIN_OIDC_REQUIRED_ROLE"):
        load_settings({**BASE, "ADMIN_OIDC_ISSUER": "https://id.example.org/realms/x", "ADMIN_OIDC_CLIENT_ID": "c"})
    with pytest.raises(ConfigError, match="https"):
        load_settings(
            {**BASE, "ADMIN_OIDC_ISSUER": "http://id.example.org/realms/x", "ADMIN_OIDC_CLIENT_ID": "c",
             "ADMIN_OIDC_REQUIRED_ROLE": "r"}
        )
    with pytest.raises(ConfigError, match="ADMIN_ROUTER_URL"):
        load_settings({**BASE, "ADMIN_ROUTER_URL": "router:8090"})


def test_configuration_defaults():
    settings = load_settings({**BASE, "ADMIN_LOCAL_PASSWORD": "a-long-local-password", "ADMIN_LOCAL_LOGIN": "off"})
    assert settings.local_enabled is False and settings.oidc_enabled is False
    assert settings.oidc_role_claim == "realm_access.roles"
    assert len(settings.session_secret) >= 32
    settings = load_settings(
        {**BASE, "ADMIN_OIDC_ISSUER": "https://id.example.org/realms/x/", "ADMIN_OIDC_CLIENT_ID": "c",
         "ADMIN_OIDC_REQUIRED_ROLE": "router-admin", "ADMIN_OIDC_ROLE_CLAIM": "resource_access.c.roles"}
    )
    assert settings.oidc_enabled and settings.oidc_issuer == "https://id.example.org/realms/x"
    assert settings.oidc_role_claim == "resource_access.c.roles"
