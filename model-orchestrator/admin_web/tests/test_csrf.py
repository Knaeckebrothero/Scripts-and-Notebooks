"""Every state-changing request needs the session's CSRF token."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from .helpers import LOCAL_PASSWORD, LOCAL_USER, add_key, audit_rows, csrf_token, sign_in

FORM = {"name": "new-key", "owner": "Someone", "rate_limit_rpm": "60", "access": "all", "submission_id": "s1"}
STATE_CHANGING = [
    ("/keys/new", FORM),
    ("/keys/1/edit", FORM),
    ("/keys/1/enabled", {"enabled": "0"}),
    ("/keys/1/delete", {"confirm_name": "test-key"}),
    ("/logout", {}),
]


def unchanged(db) -> bool:
    rows = db.execute("SELECT name, enabled FROM api_keys ORDER BY id").fetchall()
    changes = [r for r in audit_rows(db) if r["action"] != "auth.login"]
    return rows == [{"name": "test-key", "enabled": True}] and changes == []


@pytest.mark.parametrize("path,form", STATE_CHANGING)
def test_post_without_a_token_is_refused(admin: TestClient, db, path: str, form: dict):
    add_key(db)
    response = admin.post(path, data=form)
    assert response.status_code == 403
    assert "This form has expired" in response.text
    assert unchanged(db)
    assert admin.get("/keys").status_code == 200, "still signed in: the refused sign-out did nothing"


@pytest.mark.parametrize("path,form", STATE_CHANGING)
def test_post_with_a_wrong_token_is_refused(admin: TestClient, db, path: str, form: dict):
    add_key(db)
    assert admin.post(path, data={**form, "csrf_token": "not-the-token"}).status_code == 403
    assert admin.post(path, data=form, headers={"X-CSRF-Token": "not-the-token"}).status_code == 403
    assert unchanged(db)


def test_a_token_from_another_session_is_refused(admin: TestClient, make_client, db):
    add_key(db)
    attacker = make_client()
    sign_in(attacker)
    foreign = csrf_token(attacker)
    assert foreign != csrf_token(admin)
    assert admin.post("/keys/1/enabled", data={"csrf_token": foreign, "enabled": "0"}).status_code == 403
    assert unchanged(db)


def test_cross_site_requests_are_refused_even_with_a_token(admin: TestClient, db):
    add_key(db)
    token = csrf_token(admin)
    response = admin.post(
        "/keys/1/enabled", data={"csrf_token": token, "enabled": "0"}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert response.status_code == 403
    assert unchanged(db)
    same = admin.post(
        "/keys/1/enabled", data={"csrf_token": token, "enabled": "0"}, headers={"Sec-Fetch-Site": "same-origin"}
    )
    assert same.status_code == 303


def test_htmx_sends_the_token_as_a_header(admin: TestClient, db):
    add_key(db)
    token = csrf_token(admin)
    response = admin.post(
        "/keys/1/enabled",
        data={"enabled": "0", "return_to": "/keys?status=active"},
        headers={"X-CSRF-Token": token, "HX-Request": "true"},
    )
    assert response.status_code == 303 and response.headers["location"] == "/keys?status=active"
    assert db.execute("SELECT enabled FROM api_keys").fetchone() == {"enabled": False}


def test_every_page_wires_the_token_for_htmx_and_forms(admin: TestClient, db):
    add_key(db)
    token = csrf_token(admin)
    for path in ("/", "/keys", "/keys/new", "/keys/1", "/keys/1/edit", "/keys/1/delete", "/usage", "/models", "/audit"):
        page = admin.get(path).text
        assert f'"X-CSRF-Token": "{token}"' in page, path
        forms = page.count('method="post"')
        assert page.count(f'name="csrf_token" value="{token}"') == forms, f"{path}: a POST form lacks the token"


def test_sign_in_form_is_protected_too(client: TestClient):
    response = client.post("/login", data={"username": LOCAL_USER, "password": LOCAL_PASSWORD})
    assert response.status_code == 403
    assert client.get("/").status_code == 303


def test_json_or_odd_content_types_cannot_bypass_the_check(admin: TestClient, db):
    add_key(db)
    response = admin.post("/keys/1/enabled", content='{"enabled": "0"}', headers={"Content-Type": "text/plain"})
    assert response.status_code == 403
    assert unchanged(db)


def test_oversized_bodies_are_refused_before_they_are_read(client: TestClient):
    huge = "x" * (64 * 1024 + 1)
    response = client.post("/login", data={"username": huge, "password": "p"})
    assert response.status_code == 413
    assert client.post("/login", content=b"abc", headers={"Transfer-Encoding": "chunked"}).status_code in (403, 413)
