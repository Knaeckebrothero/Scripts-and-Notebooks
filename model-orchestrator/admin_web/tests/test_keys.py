"""Key management: create with a show-once secret, edit, enable or disable,
delete, and the audit row each change leaves behind."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

from mo_admin.keys import generate_token, hash_token, key_status

from .helpers import LOCAL_ACTOR, add_daily, add_hourly, add_key, add_route, audit_rows, post

TOKEN = re.compile(r"sk-mo-[0-9a-f]{40}")
FORM = {
    "name": "rag-pipeline", "owner": "Mira Castellan", "owner_email": "mira@example.org",
    "rate_limit_rpm": "600", "max_concurrency": "8", "access": "all", "expires_on": "",
    "notes": "", "submission_id": "form-1",
}


def key_changes(db) -> list[dict]:
    return [r for r in audit_rows(db) if r["action"].startswith("key.")]


def create(admin: TestClient, **overrides) -> tuple[str, int]:
    response = post(admin, "/keys/new", {**FORM, **overrides})
    assert response.status_code == 201, response.text
    tokens = set(TOKEN.findall(response.text))
    assert len(tokens) == 1, "the page shows exactly one secret"
    key_id = int(re.search(r'href="/keys/(\d+)"', response.text).group(1))
    return tokens.pop(), key_id


def test_token_format_and_hash():
    token = generate_token()
    assert re.fullmatch(r"sk-mo-[0-9a-f]{40}", token) and len(token) == 46
    assert generate_token() != token
    assert hash_token(token) == hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_create_stores_only_the_hash_and_shows_the_secret_once(admin: TestClient, db):
    token, key_id = create(admin)
    row = db.execute("SELECT * FROM api_keys WHERE id = %s", (key_id,)).fetchone()
    assert row["key_hash"] == hashlib.sha256(token.encode()).hexdigest(), "what the router will look up"
    assert row["key_prefix"] == token[:12] and row["key_prefix"].startswith("sk-mo-")
    assert (row["name"], row["owner"], row["owner_email"]) == ("rag-pipeline", "Mira Castellan", "mira@example.org")
    assert (row["rate_limit_rpm"], row["max_concurrency"]) == (600, 8)
    assert row["allowed_models"] is None and row["expires_at"] is None and row["notes"] is None
    assert row["enabled"] is True and row["created_by"] == LOCAL_ACTOR and row["last_used_at"] is None

    # Nowhere else: not on the key's pages, not in the list, not in the audit log.
    for path in (f"/keys/{key_id}", f"/keys/{key_id}/edit", "/keys", "/audit", "/"):
        page = admin.get(path)
        assert page.status_code == 200 and not TOKEN.search(page.text), path
    stored = json.dumps(db.execute("SELECT * FROM admin_audit").fetchall(), default=str)
    assert token not in stored and row["key_hash"] not in stored


def test_create_writes_an_audit_row(admin: TestClient, db):
    token, key_id = create(admin, expires_on="2031-05-06", notes="for the library")
    (entry,) = key_changes(db)
    assert (entry["actor"], entry["action"], entry["target"]) == (LOCAL_ACTOR, "key.create", f"key:{key_id}")
    assert entry["detail"] == {
        "name": "rag-pipeline", "owner": "Mira Castellan", "owner_email": "mira@example.org",
        "key_prefix": token[:12], "rate_limit_rpm": 600, "max_concurrency": 8, "allowed_models": None,
        "expires_at": "2031-05-06T23:59:59+00:00", "has_notes": True,
    }
    row = db.execute("SELECT expires_at, notes FROM api_keys").fetchone()
    assert row["expires_at"] == datetime(2031, 5, 6, 23, 59, 59, tzinfo=timezone.utc), "end of that day, UTC"
    assert row["notes"] == "for the library"


def test_secret_page_is_never_cached_and_offers_copy(admin: TestClient, db):
    response = post(admin, "/keys/new", FORM)
    assert response.headers["cache-control"] == "no-store"
    assert 'data-copy="#secret"' in response.text and 'id="secret"' in response.text
    assert "only time" in response.text


def test_resubmitting_the_form_does_not_create_a_second_key(admin: TestClient, db):
    create(admin)
    again = post(admin, "/keys/new", FORM)
    assert again.status_code == 303 and again.headers["location"] == "/keys"
    assert db.execute("SELECT count(*) AS n FROM api_keys").fetchone()["n"] == 1
    assert "already submitted" in admin.get("/keys").text
    assert len(key_changes(db)) == 1


def test_create_validates_input(admin: TestClient, db):
    bad = post(admin, "/keys/new", {**FORM, "name": " ", "owner": "", "rate_limit_rpm": "0",
                                    "max_concurrency": "many", "owner_email": "not-an-address",
                                    "expires_on": "31.12.2030"})
    assert bad.status_code == 422
    for message in ("Give the key a name.", "Say who owns the key.", "whole number from 1 to 1,000,000",
                    "or leave it empty for no cap", "name@example.org", "YYYY-MM-DD"):
        assert message in bad.text, message
    assert 'value="not-an-address"' in bad.text, "what was typed is kept"
    assert db.execute("SELECT count(*) AS n FROM api_keys").fetchone()["n"] == 0
    assert key_changes(db) == []


def test_allowed_routes(admin: TestClient, db):
    for name in ("Gemma", "Embed", "Whisper"):
        add_route(db, name)
    _, key_id = create(admin, access="some", allowed_models=["Whisper", "Embed"])
    assert db.execute("SELECT allowed_models FROM api_keys").fetchone()["allowed_models"] == ["Embed", "Whisper"]

    unknown = post(admin, "/keys/new", {**FORM, "submission_id": "f2", "access": "some", "allowed_models": ["Nope"]})
    assert unknown.status_code == 422 and "Unknown route: Nope" in unknown.text
    none = post(admin, "/keys/new", {**FORM, "submission_id": "f3", "access": "some"})
    assert none.status_code == 422 and "Pick at least one route" in none.text

    # "All routes" wins over stray ticks and is stored as NULL, which the router reads as "all".
    create(admin, submission_id="f4", name="open", access="all", allowed_models=["Gemma"])
    assert db.execute("SELECT allowed_models FROM api_keys WHERE name = 'open'").fetchone()["allowed_models"] is None


def test_edit_records_what_changed(admin: TestClient, db):
    add_route(db, "Gemma")
    _, key_id = create(admin, notes="first")
    response = post(admin, f"/keys/{key_id}/edit", {
        **FORM, "rate_limit_rpm": "900", "max_concurrency": "", "expires_on": "2030-01-31",
        "access": "some", "allowed_models": ["Gemma"], "notes": "second, with a secret-looking word",
    })
    assert response.status_code == 303 and response.headers["location"] == f"/keys/{key_id}"
    row = db.execute("SELECT * FROM api_keys").fetchone()
    assert (row["rate_limit_rpm"], row["max_concurrency"], row["allowed_models"]) == (900, None, ["Gemma"])

    entry = key_changes(db)[-1]
    assert (entry["actor"], entry["action"], entry["target"]) == (LOCAL_ACTOR, "key.update", f"key:{key_id}")
    assert entry["detail"]["changes"] == {
        "rate_limit_rpm": {"from": 600, "to": 900},
        "max_concurrency": {"from": 8, "to": None},
        "allowed_models": {"from": None, "to": ["Gemma"]},
        "expires_at": {"from": None, "to": "2030-01-31T23:59:59+00:00"},
        "notes": "changed",
    }
    assert "secret-looking" not in json.dumps(entry["detail"]), "note text stays out of the log"
    assert "Saved changes to rag-pipeline." in admin.get(f"/keys/{key_id}").text


def test_edit_without_changes_writes_nothing(admin: TestClient, db):
    _, key_id = create(admin)
    before = db.execute("SELECT key_hash FROM api_keys").fetchone()
    assert post(admin, f"/keys/{key_id}/edit", FORM).status_code == 303
    assert len(key_changes(db)) == 1
    assert db.execute("SELECT key_hash FROM api_keys").fetchone() == before, "editing never touches the secret"
    assert "Nothing changed." in admin.get(f"/keys/{key_id}").text


def test_edit_keeps_routes_the_router_no_longer_has(admin: TestClient, db):
    add_route(db, "Gemma")
    key_id = add_key(db, allowed_models=["Gemma", "Retired-Route"])
    page = admin.get(f"/keys/{key_id}/edit").text
    assert 'value="Retired-Route" checked' in page and "no longer configured" in page
    kept = post(admin, f"/keys/{key_id}/edit", {**FORM, "access": "some", "allowed_models": ["Gemma", "Retired-Route"]})
    assert kept.status_code == 303
    assert db.execute("SELECT allowed_models FROM api_keys").fetchone()["allowed_models"] == ["Gemma", "Retired-Route"]


def test_disable_and_enable(admin: TestClient, db):
    key_id = add_key(db, name="batch")
    off = post(admin, f"/keys/{key_id}/enabled", {"enabled": "0"})
    assert off.status_code == 303 and off.headers["location"] == f"/keys/{key_id}"
    assert db.execute("SELECT enabled FROM api_keys").fetchone()["enabled"] is False
    assert "Disabled batch." in admin.get("/keys").text

    post(admin, f"/keys/{key_id}/enabled", {"enabled": "0"})  # again: nothing to do, nothing logged
    post(admin, f"/keys/{key_id}/enabled", {"enabled": "1"})
    assert db.execute("SELECT enabled FROM api_keys").fetchone()["enabled"] is True
    assert [(e["action"], e["target"], e["actor"]) for e in key_changes(db)] == [
        ("key.disable", f"key:{key_id}", LOCAL_ACTOR),
        ("key.enable", f"key:{key_id}", LOCAL_ACTOR),
    ]
    assert post(admin, f"/keys/{key_id}/enabled", {"enabled": "maybe"}).status_code == 404
    assert post(admin, "/keys/999/enabled", {"enabled": "0"}).status_code == 404


def test_delete_needs_the_name_typed(admin: TestClient, db):
    key_id = add_key(db, name="batch-import", owner="Platform Team")
    add_daily(db, key_id, "Gemma", 40, 2)
    page = admin.get(f"/keys/{key_id}/delete")
    assert "Deleting cannot be undone." in page.text and "Disable instead" in page.text

    for typed in ("", "batch", "BATCH-IMPORT", "batch-import-2"):
        wrong = post(admin, f"/keys/{key_id}/delete", {"confirm_name": typed})
        assert wrong.status_code == 422 and "Nothing was deleted." in wrong.text
    assert db.execute("SELECT count(*) AS n FROM api_keys").fetchone()["n"] == 1
    assert key_changes(db) == []

    done = post(admin, f"/keys/{key_id}/delete", {"confirm_name": "batch-import"})
    assert done.status_code == 303 and done.headers["location"] == "/keys"
    assert db.execute("SELECT count(*) AS n FROM api_keys").fetchone()["n"] == 0
    (entry,) = key_changes(db)
    assert (entry["actor"], entry["action"], entry["target"]) == (LOCAL_ACTOR, "key.delete", f"key:{key_id}")
    assert entry["detail"]["name"] == "batch-import" and entry["detail"]["owner"] == "Platform Team"
    assert "key_hash" not in entry["detail"]
    assert db.execute("SELECT request_count FROM usage_daily").fetchone()["request_count"] == 40, "history is kept"
    assert "Deleted batch-import." in admin.get("/keys").text
    assert admin.get(f"/keys/{key_id}").status_code == 404
    assert post(admin, f"/keys/{key_id}/delete", {"confirm_name": "batch-import"}).status_code == 404


def test_delete_is_only_reachable_by_post(admin: TestClient, db):
    key_id = add_key(db)
    assert admin.get(f"/keys/{key_id}/delete?confirm_name=test-key").status_code == 200
    assert db.execute("SELECT count(*) AS n FROM api_keys").fetchone()["n"] == 1


def test_unknown_keys_are_404(admin: TestClient, db):
    for path in ("/keys/7", "/keys/7/edit", "/keys/7/delete", "/keys/abc", "/keys/1e3", "/keys/-1"):
        assert admin.get(path).status_code == 404, path


def test_key_status():
    now = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)

    def status(**row):
        return key_status({"enabled": True, "expires_at": None, **row}, now).code

    assert status() == "active"
    assert status(enabled=False) == "disabled"
    assert status(enabled=False, expires_at=now - timedelta(days=1)) == "disabled"
    assert status(expires_at=now - timedelta(seconds=1)) == "expired"
    assert status(expires_at=now + timedelta(days=13)) == "expiring"
    assert status(expires_at=now + timedelta(days=15)) == "active"


def names_in_order(page: str) -> list[str]:
    return re.findall(r'<a class="cell-main" href="/keys/\d+">([^<]+)</a>', page)


def test_list_search_filter_and_sort(admin: TestClient, db):
    now = datetime.now(timezone.utc)
    busy = add_key(db, name="alpha-busy", owner="Mira Castellan", owner_email="mira@example.org",
                   last_used_at=now - timedelta(hours=2), created_at=now - timedelta(days=30))
    add_key(db, name="bravo-expired", owner="Teaching Lab", expires_at=now - timedelta(days=3),
            last_used_at=now - timedelta(days=4), created_at=now - timedelta(days=20))
    add_key(db, name="charlie-soon", owner="Teaching Lab", expires_at=now + timedelta(days=5),
            last_used_at=now - timedelta(days=1), created_at=now - timedelta(days=10))
    add_key(db, name="delta-off", owner="Platform Team", enabled=False, created_at=now - timedelta(days=5))
    add_key(db, name="echo-new", owner="Platform Team", created_at=now - timedelta(days=1))
    add_daily(db, busy, "Gemma", 1234, 12, days_ago=3)
    add_daily(db, busy, "Gemma", 99, days_ago=45)  # outside the 30-day column
    add_hourly(db, busy, "Gemma", "gemma", "ok", 10, prompt=7000, completion=3000, days_ago=2)

    page = admin.get("/keys").text
    assert names_in_order(page) == ["echo-new", "delta-off", "charlie-soon", "bravo-expired", "alpha-busy"]
    assert "1,234" in page and "10K" in page and "1,333" not in page
    for label, count in (("All", 5), ("Active", 2), ("Expiring soon", 1), ("Expired", 1), ("Disabled", 1), ("Never used", 2)):
        assert re.search(rf">{label} <b>{count}</b>", page), label
    assert page.count("Never used</span>") == 2 and "Expires soon" in page and ">Expired</span>" in page

    assert names_in_order(admin.get("/keys?status=expired").text) == ["bravo-expired"]
    assert names_in_order(admin.get("/keys?status=expiring").text) == ["charlie-soon"]
    assert names_in_order(admin.get("/keys?status=disabled").text) == ["delta-off"]
    assert names_in_order(admin.get("/keys?status=unused").text) == ["echo-new", "delta-off"]
    assert names_in_order(admin.get("/keys?q=teaching").text) == ["charlie-soon", "bravo-expired"]
    assert names_in_order(admin.get("/keys?q=MIRA@example").text) == ["alpha-busy"]
    assert names_in_order(admin.get("/keys?q=platform&status=disabled").text) == ["delta-off"]
    assert names_in_order(admin.get("/keys?sort=name").text)[0] == "alpha-busy"
    assert names_in_order(admin.get("/keys?sort=name&dir=desc").text)[0] == "echo-new"
    assert names_in_order(admin.get("/keys?sort=requests").text)[0] == "alpha-busy"
    assert names_in_order(admin.get("/keys?sort=expires").text)[:2] == ["bravo-expired", "charlie-soon"]
    assert 'aria-sort="ascending"' in admin.get("/keys?sort=name").text

    nothing = admin.get("/keys?q=zzz").text
    assert "No key matches" in nothing and names_in_order(nothing) == []
    assert admin.get("/keys?sort=bogus&status=bogus&dir=sideways").status_code == 200


def test_empty_list_invites_the_first_key(admin: TestClient, db):
    page = admin.get("/keys").text
    assert "No keys yet" in page and 'href="/keys/new"' in page


def test_key_detail_shows_settings_usage_and_history(admin: TestClient, db):
    add_route(db, "Gemma")
    token, key_id = create(admin, notes="call Mira first")
    add_daily(db, key_id, "Gemma", 480, 12, days_ago=1)
    add_hourly(db, key_id, "Gemma", "gemma-4", "ok", 460, prompt=90_000, completion=30_000, duration_ms=920_000, days_ago=1)
    add_hourly(db, key_id, "Gemma", "gemma-4", "backend_error", 12, days_ago=1)
    add_hourly(db, key_id, "Gemma", "gemma-4", "rate_limited", 5, days_ago=1)
    post(admin, f"/keys/{key_id}/enabled", {"enabled": "0"})

    page = admin.get(f"/keys/{key_id}").text
    for text in ("rag-pipeline", token[:12], "Mira Castellan", "600 requests per minute", "8 requests at once",
                 "All routes", "call Mira first", "Created key", "Disabled key", "This key is disabled.",
                 "gemma-4", "Completed", "Backend error (5xx)", "Rate limited", "120K"):
        assert text in page, text
    assert admin.get(f"/keys/{key_id}?range=90d").status_code == 200
    assert admin.get(f"/keys/{key_id}?range=7d").status_code == 200
    assert admin.get(f"/keys/{key_id}?range=nonsense").status_code == 200
