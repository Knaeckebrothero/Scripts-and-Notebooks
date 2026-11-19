"""Postgres: degraded mode, the tables other tools read, adopting an existing database, migrations.

Not covered: "a database that is down at startup still fails the start"
(design 7.10). The legacy router waits 60 s for Postgres before it gives up,
which would add a minute to every run.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tests.harness.router import ROUTER_DIR
from tests.harness.stack import route
from tests.harness.util import holds_for, wait_until

CHAT = "/v1/chat/completions"
MIGRATIONS = sorted((ROUTER_DIR / "migrations").glob("*.sql"))


def solo_routes(backend):
    return [route("Solo", "chat", backend, models=["solo"], backend_model_name="acme/solo")]


@pytest.fixture
def backend(factory):
    return factory.fake("vllm", ["acme/solo"])


# ---- degraded mode -----------------------------------------------------------


@pytest.mark.timeout(120)
def test_requests_keep_authenticating_while_the_database_is_away(factory, backend):
    stack = factory.stack(solo_routes(backend), db_via_relay=True, name="degraded")
    restricted = stack.new_key(allowed=["Elsewhere"])
    assert stack.chat("solo").status_code == 200

    stack.relay.stop()  # the router's connections are cut and new ones are refused

    def still_serving():
        return (
            stack.chat("solo").status_code == 200
            and stack.chat("solo", key="sk-mo-unknown").status_code == 401
            and stack.chat("solo", key=restricted).status_code == 403
        )

    holds_for(
        still_serving, seconds=3.5, interval=0.25, what="auth from the cached key set"
    )  # > 3 refresh periods
    assert stack.get("/livez", key=None).status_code == 200
    assert stack.get("/health", key=None).status_code == 200
    assert stack.router.running

    # ... and when the database is back, key changes are picked up again
    stack.relay.start()
    fresh = stack.db.create_key()
    wait_until(lambda: stack.authenticates(fresh), timeout=60, interval=0.25, what="key refresh to resume")


@pytest.mark.legacy_only
def test_health_says_degraded_while_the_database_is_away_today(factory, backend):
    stack = factory.stack(solo_routes(backend), db_via_relay=True, name="degraded-health")
    assert stack.get("/health", key=None).json()["status"] == "healthy"

    stack.relay.stop()

    body = wait_until(
        lambda: (b := stack.get("/health", key=None).json())["status"] == "degraded" and b,
        timeout=10, what="/health to report the outage",
    )  # fmt: skip
    assert body["database"]["status"] == "unavailable"
    assert body["database"]["keys_loaded"] == 1


# ---- a fresh database ----------------------------------------------------------


def test_empty_database_is_initialised_and_denies_everything(factory, backend):
    stack = factory.stack(solo_routes(backend), schema=None, name="virgin")

    assert {"api_keys", "usage_daily", "routes"} <= stack.db.tables()
    assert stack.db.scalar("SELECT count(*) FROM api_keys") == 0
    assert stack.chat("solo", key="sk-mo-anything").status_code == 401
    assert stack.chat("solo", key=None).status_code == 401

    key = stack.db.create_key()  # the first key, as the admin UI would create it

    wait_until(lambda: stack.authenticates(key), timeout=5, what="the first key to be picked up")
    assert stack.chat("solo", key=key).status_code == 200


def test_api_keys_table_has_the_columns_the_admin_ui_uses(factory, backend):
    stack = factory.stack(solo_routes(backend), schema=None, name="virgin-columns")

    assert {
        "id", "key_hash", "key_prefix", "name", "owner", "rate_limit_rpm", "allowed_models",
        "enabled", "expires_at", "created_at", "last_used_at",
    } <= stack.db.columns("api_keys")  # fmt: skip
    assert {"day", "api_key_id", "owner", "model", "request_count", "error_count"} <= stack.db.columns(
        "usage_daily"
    )
    assert {"name", "type", "updated_at"} <= stack.db.columns("routes")


def test_routes_table_mirrors_the_config(factory):
    """The admin UI reads ``routes`` to offer the allowlist picker."""
    chat, embed = factory.fake("vllm", ["acme/solo"]), factory.fake("tei")
    stack = factory.stack(
        [
            route("Solo", "chat", chat, models=["solo"]),
            route("Vectors", "embedding", embed, models=["vectors"]),
        ],
        start=False,
    )
    stack.db.execute("INSERT INTO routes (name, type) VALUES ('Gone-Route', 'chat'), ('Vectors', 'chat')")

    stack.router.start()

    rows = {r["name"]: r["type"] for r in stack.db.execute("SELECT name, type FROM routes")}
    assert rows == {"Solo": "chat", "Vectors": "embedding"}  # stale row removed, wrong type corrected


# ---- adopting a database the old router ran on -----------------------------------


def seed_legacy_data(db):
    """Keys and usage as the legacy router and the admin UI left them."""
    key = db.create_key(name="old-key", owner="old-owner", rpm=7, allowed=["Solo"])
    disabled = db.create_key(name="old-disabled", enabled=False)
    db.execute(
        "INSERT INTO usage_daily (day, api_key_id, owner, model, request_count, error_count) VALUES "
        "(%s, %s, 'old-owner', 'Solo', 120, 3), (%s, %s, 'old-owner', 'Retired-Route', 40, 0), "
        "(CURRENT_DATE, %s, 'old-owner', 'Solo', 5, 1), (%s, 9999, 'deleted-owner', 'Solo', 77, 7)",
        (dt.date(2026, 6, 12), key.id, dt.date(2026, 6, 13), key.id, key.id, dt.date(2026, 6, 14)),
    )
    db.execute("INSERT INTO routes (name, type) VALUES ('Retired-Route', 'chat')")
    return key, disabled


def test_existing_database_is_adopted_with_its_keys_and_usage(factory, backend):
    stack = factory.stack(solo_routes(backend), start=False, name="adopt")
    old, disabled = seed_legacy_data(stack.db)
    before = stack.db.execute("SELECT * FROM usage_daily WHERE day < CURRENT_DATE ORDER BY day")
    keys_before = stack.db.execute("SELECT * FROM api_keys ORDER BY id")

    stack.router.start()

    r = stack.chat("solo", key=old)
    assert r.status_code == 200
    assert r.headers["x-ratelimit-limit"] == "7"
    assert stack.chat("solo", key=disabled).status_code == 401
    assert stack.db.execute("SELECT * FROM usage_daily WHERE day < CURRENT_DATE ORDER BY day") == before
    legacy_columns = keys_before[0].keys()
    keys_after = stack.db.execute("SELECT * FROM api_keys ORDER BY id")
    assert [{c: row[c] for c in legacy_columns if c != "last_used_at"} for row in keys_after] == [
        {c: row[c] for c in legacy_columns if c != "last_used_at"} for row in keys_before
    ]
    # today's row is added to, not replaced: 5 before plus the request above
    wait_until(
        lambda: stack.db.usage_daily(old).get("Solo", (0, 0))[0] == 120 + 5 + 1,
        timeout=5, what="today's usage row to be incremented",
    )  # fmt: skip


def test_restart_on_the_same_database_keeps_everything(factory, backend):
    stack = factory.stack(solo_routes(backend), name="restart")
    key = stack.new_key(rpm=9)
    assert stack.chat("solo", key=key).status_code == 200
    wait_until(lambda: stack.db.usage_daily(key).get("Solo") == (1, 0), timeout=5, what="usage to be written")

    stack.router.restart()

    r = stack.chat("solo", key=key)  # known from the first key load, no refresh needed
    assert r.status_code == 200
    assert r.headers["x-ratelimit-remaining"] == "8"  # the in-memory window started over
    wait_until(lambda: stack.db.usage_daily(key).get("Solo") == (2, 0), timeout=5, what="usage to add up")


# ---- migrations (design 7.10) -----------------------------------------------------


def applied_migrations(db) -> int:
    return db.scalar("SELECT count(*) FROM schema_migrations")


def assert_current_schema(db) -> None:
    assert {
        "api_keys",
        "usage_daily",
        "routes",
        "usage_hourly",
        "admin_audit",
        "schema_migrations",
    } <= db.tables()
    assert {"max_concurrency", "owner_email", "created_by", "notes"} <= db.columns("api_keys")
    assert {
        "hour", "api_key_id", "owner", "model", "route", "outcome",
        "requests", "prompt_tokens", "completion_tokens", "duration_ms_sum",
    } <= db.columns("usage_hourly")  # fmt: skip
    assert applied_migrations(db) == len(MIGRATIONS)


@pytest.mark.feature("migrations")
def test_migrations_build_the_schema_on_an_empty_database(factory, backend):
    stack = factory.stack(solo_routes(backend), schema=None, name="migrate-empty")

    assert_current_schema(stack.db)


@pytest.mark.feature("migrations")
def test_migrations_adopt_a_legacy_database_with_data(factory, backend):
    stack = factory.stack(solo_routes(backend), start=False, name="migrate-legacy")
    old, _ = seed_legacy_data(stack.db)
    usage_before = stack.db.execute("SELECT * FROM usage_daily ORDER BY day, api_key_id, model")
    assert "schema_migrations" not in stack.db.tables()

    stack.router.start()

    assert_current_schema(stack.db)
    assert stack.db.execute("SELECT * FROM usage_daily ORDER BY day, api_key_id, model") == usage_before
    row = stack.db.execute("SELECT * FROM api_keys WHERE id = %s", (old.id,))[0]
    assert (row["name"], row["owner"], row["rate_limit_rpm"], row["allowed_models"]) == (
        "old-key",
        "old-owner",
        7,
        ["Solo"],
    )
    assert row["max_concurrency"] is None and row["notes"] is None  # new columns: NULL, so no cap
    assert stack.chat("solo", key=old).status_code == 200
    assert stack.db.scalar("SELECT count(*) FROM usage_hourly WHERE api_key_id = %s", (old.id,)) is not None


@pytest.mark.feature("migrations")
def test_migrations_run_once(factory, backend):
    stack = factory.stack(solo_routes(backend), name="migrate-twice")
    first = stack.db.execute("SELECT * FROM schema_migrations ORDER BY 1")

    stack.router.restart()

    assert stack.db.execute("SELECT * FROM schema_migrations ORDER BY 1") == first
    assert stack.chat("solo").status_code == 200


def test_the_old_router_still_runs_on_a_migrated_database(factory, backend):
    """Rollback: version 3 has migrated the database, then the previous image is pinned again.

    The roadmap promises that this needs no database step, because the migrations only add.
    Both targets are pinned here, so the test means the same under either ROUTER_TARGET.
    """
    new = factory.stack(solo_routes(backend), target="new", name="rollback-new")
    key = new.new_key(rpm=11)
    # a key as the admin website writes it: the columns 2.x does not know are filled
    capped = new.new_key(max_concurrency=1)
    new.db.execute(
        "UPDATE api_keys SET owner_email = 'someone@example.org', created_by = 'local:admin', "
        "notes = 'made on the website' WHERE id = %s",
        (capped.id,),
    )
    assert new.chat("solo", key=key).status_code == 200
    wait_until(lambda: new.db.usage_daily(key).get("Solo") == (1, 0), timeout=5, what="usage to be written")
    migrations = new.db.execute("SELECT * FROM schema_migrations ORDER BY 1")
    new.router.stop()

    old = factory.stack(solo_routes(backend), target="legacy", start=False, name="rollback-old")
    old.router.database_url = new.router.database_url
    old.router.start()
    assert "x_router" not in old.get("/v1/models", key=key).json()["data"][0]  # really 2.x

    r = old.chat("solo", key=key)
    assert r.status_code == 200
    assert r.headers["x-ratelimit-limit"] == "11"
    assert old.chat("solo", key=capped).status_code == 200
    assert old.chat("solo", key="sk-mo-unknown").status_code == 401
    wait_until(lambda: new.db.usage_daily(key).get("Solo") == (2, 0), timeout=5, what="2.x to keep counting")

    # ... and forward again: nothing to migrate, everything still there
    old.router.stop()
    new.router.start()
    assert new.chat("solo", key=key).status_code == 200
    assert new.db.execute("SELECT * FROM schema_migrations ORDER BY 1") == migrations
    row = new.db.execute(
        "SELECT owner_email, notes, max_concurrency FROM api_keys WHERE id = %s", (capped.id,)
    )
    assert row == [
        {"owner_email": "someone@example.org", "notes": "made on the website", "max_concurrency": 1}
    ]


@pytest.mark.feature("migrations")
def test_two_routers_starting_at_once_do_not_trip_over_the_migrations(factory, backend):
    """The advisory lock: a second instance on the same empty database must not fail or double-apply."""
    first = factory.stack(solo_routes(backend), schema=None, start=False, name="migrate-race-a")
    second = factory.stack(solo_routes(backend), schema=None, start=False, name="migrate-race-b")
    second.router.database_url = first.router.database_url

    first.router.start(wait=False)
    second.router.start(wait=False)
    first.router.wait_live()
    second.router.wait_live()

    assert_current_schema(first.db)


def test_migration_files_are_idempotent_sql(postgres):
    """Guards the files themselves: every one can be applied twice to a legacy database."""
    db = postgres.create_database()
    try:
        db.create_legacy_schema()
        key = db.create_key(name="survivor")
        for _ in range(2):
            for path in MIGRATIONS:
                db.run_script(path)
        assert {"usage_hourly", "admin_audit"} <= db.tables()
        assert "max_concurrency" in db.columns("api_keys")
        assert db.scalar("SELECT name FROM api_keys WHERE id = %s", (key.id,)) == "survivor"
    finally:
        db.drop()


def test_legacy_schema_copy_matches_migration_0001(postgres):
    """``harness/legacy_schema.sql`` (what the old router creates) and 0001 must describe the same tables."""

    def shape(db):
        return db.execute(
            "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
        )

    one, two = postgres.create_database(), postgres.create_database()
    try:
        one.create_legacy_schema()
        two.run_script(MIGRATIONS[0])
        assert shape(one) == shape(two)
    finally:
        one.drop()
        two.drop()
