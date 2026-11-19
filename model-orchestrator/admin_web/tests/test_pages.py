"""Whole-page checks: security headers, a markup audit against the
Content-Security-Policy, escaping, the audit log, and the demo seed."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from .helpers import add_daily, add_hourly, add_key, add_route, audit_rows, post, sign_in

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["/", "/?range=24h", "/?range=30d", "/keys", "/keys?status=expired", "/keys/new", "/keys/1",
         "/keys/1?range=90d", "/keys/1/edit", "/keys/1/delete", "/usage", "/usage?group=owner",
         "/usage?group=route&by=day", "/usage?group=model", "/usage?group=outcome", "/models", "/audit",
         "/audit?kind=key", "/partials/router-lamp", "/no-such-page"]

INLINE_SCRIPT = re.compile(r"<script(?![^>]*\ssrc=)[^>]*>", re.I)
INLINE_STYLE = re.compile(r"<style|\sstyle\s*=", re.I)
INLINE_HANDLER = re.compile(r"<[^>]+\son[a-z]+\s*=", re.I)


@pytest.fixture()
def seeded(db, admin: TestClient) -> TestClient:
    add_route(db, "Gemma", "chat")
    add_route(db, "Embed", "embedding")
    key = add_key(db, name="rag", owner="Mira", allowed_models=["Gemma"])
    for days_ago in range(0, 40, 3):
        add_daily(db, key, "Gemma", 100 + days_ago, 2, days_ago=days_ago, owner="Mira")
        add_daily(db, key, "Embed", 300, 0, days_ago=days_ago, owner="Mira")
    add_hourly(db, key, "Gemma", "gemma-4", "ok", 90, prompt=1000, completion=400, duration_ms=9000, owner="Mira")
    add_hourly(db, key, "Gemma", "gemma-4", "timeout", 2, duration_ms=120000, owner="Mira")
    return admin


def test_security_headers(seeded: TestClient):
    for path in ("/", "/login", "/keys", "/usage.csv", "/livez", "/static/js/app.js", "/no-such-page"):
        headers = seeded.get(path).headers
        csp = headers["content-security-policy"]
        assert "default-src 'none'" in csp and "script-src 'self'" in csp and "style-src 'self'" in csp, path
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "*" not in csp
        assert "frame-ancestors 'none'" in csp and "form-action 'self'" in csp and "base-uri 'none'" in csp
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "same-origin"
        assert headers["cross-origin-opener-policy"] == "same-origin"
    assert seeded.get("/keys").headers["cache-control"] == "no-store"
    assert seeded.get("/login").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", PAGES)
def test_pages_render_without_anything_inline(seeded: TestClient, path: str):
    """The CSP has no 'unsafe-inline': one inline script, style attribute or
    event handler would be silently blocked in the browser."""
    response = seeded.get(path)
    assert response.status_code == (404 if path == "/no-such-page" else 200)
    html = response.text
    assert not INLINE_SCRIPT.search(html), "inline <script>"
    assert not INLINE_STYLE.search(html), "inline style"
    assert not INLINE_HANDLER.search(html), "inline event handler"
    assert "{{" not in html and "{%" not in html


def test_login_and_error_pages_have_nothing_inline(client: TestClient):
    for path in ("/login", "/login?signed_out=1", "/nope"):
        html = client.get(path).text
        assert not INLINE_SCRIPT.search(html) and not INLINE_STYLE.search(html) and not INLINE_HANDLER.search(html)


def test_static_assets_are_local_and_cacheable(client: TestClient):
    page = client.get("/login").text
    sources = re.findall(r'(?:src|href)="([^"]+)"', page)
    assert all(not s.startswith(("http:", "https:", "//")) for s in sources), "no CDN, nothing off-site"
    htmx = client.get("/static/vendor/htmx.min.js")
    assert htmx.status_code == 200 and 'version:"2.0.4"' in htmx.text
    versioned = next(s for s in sources if "app.css?v=" in s)
    assert client.get(versioned).headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/static/css/app.css").headers["cache-control"] == "public, max-age=300"
    assert client.get("/static/fonts/heros-regular.woff2?v=1").status_code == 200
    css = client.get("/static/css/app.css").text
    assert "http://" not in css and "https://" not in css
    assert client.get("/static/../config.py").status_code == 404


def test_htmx_is_configured_not_to_evaluate_or_cache(client: TestClient):
    page = client.get("/login").text
    config = re.search(r"<meta name=\"htmx-config\" content='([^']+)'", page).group(1)
    assert '"allowEval":false' in config and '"includeIndicatorStyles":false' in config
    assert '"historyCacheSize":0' in config and '"selfRequestsOnly":true' in config


def test_user_text_is_escaped_everywhere(admin: TestClient, db):
    nasty = '<img src=x onerror=alert(1)>"\'&'
    add_route(db, "Gemma")
    response = post(admin, "/keys/new", {
        "name": nasty, "owner": nasty, "owner_email": "", "rate_limit_rpm": "5", "access": "all",
        "notes": nasty, "submission_id": "x1",
    })
    assert response.status_code == 201
    add_daily(db, 1, nasty, 10, owner=nasty)
    add_hourly(db, 1, nasty, nasty, "ok", 10, owner=nasty)
    for path in ("/", "/keys", "/keys/1", "/keys/1/edit", "/keys/1/delete", "/usage", "/usage?group=owner",
                 "/usage?group=model", "/usage?group=route", "/models", "/audit", f"/keys?q={nasty}"):
        html = admin.get(path).text
        assert "<img src=x" not in html, path
        assert "&lt;img src=x onerror=alert(1)&gt;" in html, path


def test_audit_log_page(admin: TestClient, db):
    key_id = add_key(db, name="batch", owner="Platform Team")
    post(admin, f"/keys/{key_id}/enabled", {"enabled": "0"})
    post(admin, f"/keys/{key_id}/edit", {"name": "batch", "owner": "Platform Team", "rate_limit_rpm": "250",
                                         "access": "all", "submission_id": "e1"})
    page = admin.get("/audit").text
    for text in ("Signed in</span>", "Disabled key", "Edited key", "Rate limit: 100 per minute → 250 per minute",
                 "With the local account", f'href="/keys/{key_id}"'):
        assert text in page, text
    only_keys = admin.get("/audit?kind=key").text
    assert "Disabled key" in only_keys and "Signed in</span>" not in only_keys
    only_auth = admin.get("/audit?kind=auth").text
    assert "Signed in</span>" in only_auth and "Disabled key" not in only_auth
    assert "No entries match" in admin.get("/audit?actor=nobody-by-that-name").text
    assert "Disabled key" in admin.get("/audit?actor=OP").text, "the actor filter ignores case"
    assert admin.get("/audit?actor=100%25&kind=bogus").status_code == 200

    post(admin, f"/keys/{key_id}/delete", {"confirm_name": "batch"})
    page = admin.get("/audit").text
    assert "Deleted key" in page and f'href="/keys/{key_id}"' not in page, "no link to a key that is gone"


def test_audit_log_pages_backwards(admin: TestClient, db):
    for index in range(120):
        db.execute("INSERT INTO admin_audit (actor, action, target, detail) VALUES (%s, 'key.enable', 'key:1', %s)",
                   (f"actor-{index:03d}", '{"name": "k"}'))
    first = admin.get("/audit?kind=key").text
    assert "actor-119" in first and "actor-070" in first and "actor-069" not in first
    older = re.search(r'id="audit-older" href="([^"]+)"', first).group(1).replace("&amp;", "&")
    second = admin.get(older).text
    assert "actor-069" in second and "actor-020" in second and "actor-119" not in second
    third = admin.get(re.search(r'id="audit-older" href="([^"]+)"', second).group(1).replace("&amp;", "&")).text
    assert "actor-000" in third and 'id="audit-older"' not in third and 'id="audit-newest"' in third


def test_every_state_change_is_audited_with_who_and_what(admin: TestClient, db):
    post(admin, "/keys/new", {"name": "k1", "owner": "o", "rate_limit_rpm": "5", "access": "all", "submission_id": "a"})
    post(admin, "/keys/1/edit", {"name": "k1", "owner": "o2", "rate_limit_rpm": "5", "access": "all"})
    post(admin, "/keys/1/enabled", {"enabled": "0"})
    post(admin, "/keys/1/enabled", {"enabled": "1"})
    post(admin, "/keys/1/delete", {"confirm_name": "k1"})
    post(admin, "/logout")
    rows = audit_rows(db)
    assert [r["action"] for r in rows] == [
        "auth.login", "key.create", "key.update", "key.disable", "key.enable", "key.delete", "auth.logout",
    ]
    assert {r["actor"] for r in rows} == {"local:ops"}
    assert [r["target"] for r in rows] == [None, "key:1", "key:1", "key:1", "key:1", "key:1", None]
    assert all(isinstance(r["detail"], dict) and r["detail"] for r in rows)


def test_demo_seed_fills_every_page(database_url: str, db, make_client):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "seed_demo.py"), "--database-url", database_url, "--reset"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert db.execute("SELECT count(*) AS n FROM api_keys").fetchone()["n"] == 12
    assert db.execute("SELECT count(*) AS n FROM routes").fetchone()["n"] == 9
    span = db.execute("SELECT count(DISTINCT day) AS days FROM usage_daily").fetchone()["days"]
    hours = db.execute("SELECT count(DISTINCT (hour AT TIME ZONE 'UTC')::date) AS days FROM usage_hourly").fetchone()["days"]
    assert span == 60 and hours == 14

    # Where both tables cover a day they must agree, as they do when the router writes them.
    mismatch = db.execute(
        """
        SELECT d.day, d.api_key_id, d.model FROM usage_daily d
        JOIN (
            SELECT (hour AT TIME ZONE 'UTC')::date AS day, api_key_id, route,
                   SUM(requests) FILTER (WHERE outcome NOT IN ('unavailable', 'rate_limited', 'concurrency_limited')) AS forwarded,
                   SUM(requests) FILTER (WHERE outcome NOT IN ('unavailable', 'rate_limited', 'concurrency_limited', 'ok', 'client_closed')) AS errors
            FROM usage_hourly GROUP BY 1, 2, 3
        ) h ON h.day = d.day AND h.api_key_id = d.api_key_id AND h.route = d.model
        WHERE d.request_count <> COALESCE(h.forwarded, 0) OR d.error_count <> COALESCE(h.errors, 0)
        """
    ).fetchall()
    assert mismatch == []
    assert not re.search(r"sk-mo-[0-9a-f]{40}", result.stdout), "the seed never prints a usable secret"

    again = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "seed_demo.py"), "--database-url", database_url],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert again.returncode == 1 and "--reset" in again.stderr, "refuses to overwrite existing keys"

    client = make_client()
    sign_in(client)
    for path in PAGES[:-1]:
        assert client.get(path).status_code == 200, path
    home = client.get("/").text
    assert "summer-school-2026" in home and "Expired" in home and "Other" in home
