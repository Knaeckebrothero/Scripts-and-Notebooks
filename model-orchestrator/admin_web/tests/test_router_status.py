"""Live router status: both /health shapes, and a router that is not there."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from mo_admin.router_client import parse_health, parse_models

from .fakes import HEALTH_BACKENDS, HEALTH_ROUTES, FakeRouter, free_port
from .helpers import add_daily, add_key, add_route, sign_in


@pytest.fixture()
def console(make_client, serve_router, db):
    """A signed-in client whose console points at the given fake router."""

    def build(fake: FakeRouter | None = None, url: str | None = None) -> TestClient:
        client = make_client(router_url=url or serve_router(fake))
        sign_in(client)
        return client

    for name, kind in (("Gemma-4-26B-A4B", "chat"), ("Qwen3-Embedding-8B", "embedding"), ("Whisper-large-v3-turbo", "stt")):
        add_route(db, name, kind)
    key = add_key(db)
    add_daily(db, key, "Gemma-4-26B-A4B", 120, 3)
    return build


# --------------------------------------------------------------------- parsing
def test_parse_new_shape():
    status = parse_health(HEALTH_ROUTES)
    assert status.reachable and status.shape == "routes"
    assert (status.status, status.version, status.database) == ("healthy", "3.0.0", "healthy")
    assert status.routes == {"Gemma-4-26B-A4B": "up", "Qwen3-Embedding-8B": "up", "Whisper-large-v3-turbo": "down"}
    assert status.routes_down == ["Whisper-large-v3-turbo"] and status.routes_up == 2
    assert status.route_state("Whisper-large-v3-turbo") == "down" and status.route_state("Unlisted") == "unknown"
    assert status.level == "warn" and status.summary == "2 of 3 routes up"
    assert status.backends == {}


def test_parse_old_shape():
    status = parse_health(HEALTH_BACKENDS)
    assert status.reachable and status.shape == "backends"
    assert (status.status, status.version, status.database) == ("degraded", None, "unavailable")
    assert status.backends == {
        "http://127.0.0.1:8081": "healthy", "http://127.0.0.1:8082": "unhealthy",
        "http://127.0.0.1:8083": "unavailable", "http://127.0.0.1:8084": "skipped",
    }
    assert status.backends_down == ["http://127.0.0.1:8082", "http://127.0.0.1:8083"]
    assert status.routes == {} and status.route_state("Gemma-4-26B-A4B") == "unknown"
    assert status.database_ok is False and status.level == "warn"
    assert status.summary == "1 of 3 backends healthy, database unavailable"


def test_parse_is_tolerant():
    all_up = parse_health({"status": "healthy", "version": "3.1.0", "database": {"status": "healthy"},
                           "routes": {"a": "up", "b": "up"}, "something_new": [1, 2]})
    assert all_up.level == "ok" and all_up.summary == "2 of 2 routes up"
    assert parse_health({"status": "healthy"}).level == "ok"
    assert parse_health({"routes": {"a": "weird"}}).routes == {"a": "down"}
    assert parse_health({"backends": {"u": "weird"}}).backends == {"u": "unavailable"}
    assert parse_health(["not", "an", "object"]).error
    assert parse_models({"data": [{"id": "m", "x_router": {"route": "R", "type": "chat"}}, {"no": "id"}, "junk"]})[0].route == "R"
    assert parse_models({"data": "nope"}) == [] and parse_models(None) == []


# ------------------------------------------------------------------ in the app
def test_new_shape_in_the_pages(console):
    fake = FakeRouter("routes")
    client = console(fake)
    models = client.get("/models").text
    assert "Version 3.0.0" in models and "Down: Whisper-large-v3-turbo" in models
    assert models.count('class="state up"') == 2 and models.count('class="state down"') == 1
    for model_id in ("gemma-4-26b", "google/gemma-4-26B-A4B-it", "qwen3-embedding-8b"):
        assert model_id in models
    assert "Not listed while the route is down" in models
    assert "Backends" not in models and "Model ids served</h2>" not in models

    home = client.get("/").text
    assert "Whisper-large-v3-turbo is down." in home and "2 of 3 routes up" in home
    lamp = client.get("/partials/router-lamp").text
    assert "2 of 3 routes up" in lamp and 'class="lamp warn"' in lamp
    assert {"/health", "/v1/models"} <= set(fake.calls)


def test_old_shape_in_the_pages(console):
    client = console(FakeRouter("backends"))
    models = client.get("/models").text
    assert "Backends</h2>" in models and "reports health per backend, not per route" in models
    for url, word in (("http://127.0.0.1:8081", "Healthy"), ("http://127.0.0.1:8082", "Unhealthy"),
                      ("http://127.0.0.1:8083", "Unreachable"), ("http://127.0.0.1:8084", "Not probed")):
        assert url in models and word in models
    assert models.count("No status") == 3, "an older router cannot say which route is up"
    assert "Model ids served</h2>" in models and "gemma-4-26b" in models
    assert "Version not reported" in models and "Unavailable" in models
    assert "1 of 3 backends healthy, database unavailable" in client.get("/partials/router-lamp").text
    assert client.get("/").status_code == 200


def test_unreachable_router_does_not_break_anything(console):
    client = console(url=f"http://127.0.0.1:{free_port()}")
    models = client.get("/models")
    assert models.status_code == 200
    assert "The router is not answering" in models.text and "connection refused" in models.text
    assert "Gemma-4-26B-A4B" in models.text and "120" in models.text, "database-backed content is still there"
    home = client.get("/")
    assert home.status_code == 200 and "The router is not answering" in home.text
    lamp = client.get("/partials/router-lamp").text
    assert "Router unreachable" in lamp and 'class="lamp down"' in lamp
    for path in ("/keys", "/keys/new", "/usage", "/usage.csv", "/audit", "/keys/1"):
        assert client.get(path).status_code == 200, path


@pytest.mark.parametrize("status_code", [500, 404])
def test_router_errors_count_as_unreachable(console, status_code: int):
    fake = FakeRouter("routes")
    fake.health_status = status_code
    client = console(fake)
    page = client.get("/models").text
    assert "The router is not answering" in page and f"HTTP {status_code}" in page


def test_garbage_from_the_router_is_survivable(console):
    fake = FakeRouter("routes")
    fake.health = "<html>not json at all</html>"
    client = console(fake)
    assert client.get("/models").status_code == 200
    fake2 = FakeRouter("routes")
    fake2.health = {"routes": {"<script>alert(1)</script>": "up"}, "version": "<b>x</b>"}
    page = console(fake2).get("/models").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_model_list_failure_keeps_the_health(console):
    fake = FakeRouter("routes")
    fake.models_status = 503
    page = console(fake).get("/models").text
    assert "The model list failed: HTTP 503" in page and "Version 3.0.0" in page


def test_status_is_cached_between_page_loads(console):
    fake = FakeRouter("routes")
    client = console(fake)
    for _ in range(4):
        client.get("/models")
        client.get("/partials/router-lamp")
    assert fake.calls.count("/health") == 1, "one fetch serves pages for a few seconds"


def test_without_a_router_url(admin: TestClient, db):
    add_route(db, "Gemma", "chat")
    page = admin.get("/models").text
    assert "Live status is off." in page and "ADMIN_ROUTER_URL" in page and "Gemma" in page
    assert "Router status is not configured" in admin.get("/partials/router-lamp").text
    assert "ADMIN_ROUTER_URL" in admin.get("/").text
