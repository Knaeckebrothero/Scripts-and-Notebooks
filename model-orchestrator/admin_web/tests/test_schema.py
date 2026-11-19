"""The website never migrates: it reports what is missing, or that the
database is down, and keeps sign-in working either way."""
from __future__ import annotations

from .conftest import migration_files, temporary_schema
from .fakes import free_port
from .helpers import sign_in

PAGES = ("/", "/keys", "/keys/new", "/usage", "/usage.csv", "/models", "/audit")


def test_empty_database_shows_the_schema_page(postgres_url, make_client):
    with temporary_schema(postgres_url, []) as empty:
        client = make_client(database_url=empty)
        assert client.get("/livez").status_code == 200
        assert client.get("/").status_code == 303, "anonymous visitors learn nothing about the schema"
        assert sign_in(client).status_code == 303, "signing in does not need the tables"
        for path in PAGES:
            page = client.get(path)
            assert page.status_code == 503, path
            assert "The database schema is not initialised" in page.text
        page = client.get("/").text
        for table in ("api_keys", "routes", "usage_daily", "usage_hourly", "admin_audit"):
            assert f'<span class="tag">{table}</span>' in page
        assert "start the router once" in page


def test_half_migrated_database_names_what_is_missing(postgres_url, make_client):
    baseline = migration_files()[:1]
    with temporary_schema(postgres_url, baseline) as old:
        client = make_client(database_url=old)
        sign_in(client)
        page = client.get("/keys")
        assert page.status_code == 503
        for name in ("api_keys.max_concurrency", "api_keys.owner_email", "api_keys.created_by", "api_keys.notes",
                     "usage_hourly", "admin_audit"):
            assert f'<span class="tag">{name}</span>' in page.text, name
        assert '<span class="tag">api_keys</span>' not in page.text
        assert '<span class="tag">usage_daily</span>' not in page.text


def test_pages_work_as_soon_as_the_migrations_ran(postgres_url, make_client):
    with temporary_schema(postgres_url, []) as conninfo:
        client = make_client(database_url=conninfo)
        sign_in(client)
        assert client.get("/keys").status_code == 503
        import psycopg

        with psycopg.connect(conninfo, autocommit=True) as conn:
            for path in migration_files():
                conn.execute(path.read_text(encoding="utf-8"))
        assert client.get("/keys").status_code == 200, "no restart needed"


def test_migrations_apply_twice(postgres_url):
    """The files are written to be adopted by an existing database."""
    with temporary_schema(postgres_url, migration_files() + migration_files()):
        pass


def test_unreachable_database(make_client):
    client = make_client(
        database_url=f"postgresql://dbuser-xq:dbpass-zv@127.0.0.1:{free_port()}/nowhere", database_timeout=1.0
    )
    assert client.get("/livez").status_code == 200
    assert client.get("/login").status_code == 200
    assert sign_in(client).status_code == 303, "the break-glass sign-in works without the database"
    page = client.get("/keys")
    assert page.status_code == 503
    assert "The database cannot be reached" in page.text and page.headers["retry-after"] == "10"
    assert "dbuser-xq" not in page.text and "dbpass-zv" not in page.text, "no connection details on the page"
