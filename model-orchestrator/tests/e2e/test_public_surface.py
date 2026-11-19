"""``/livez``, ``/health``, ``/metrics`` and the docs endpoints. C8, C9, C10."""

from __future__ import annotations

import json
import time

import pytest

from tests.harness import world as w
from tests.harness.stack import route
from tests.harness.util import error_of, find_values

DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def test_livez(stack, world):
    r = stack.get("/livez", key=None)

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_livez_does_not_touch_backends_or_wait_for_them(stack, world):
    for fake in world.fakes:
        fake.hang(path="/health")
    started = time.monotonic()

    assert stack.get("/livez", key=None).status_code == 200

    assert time.monotonic() - started < 1.0


def test_health_is_public_and_answers_200_with_a_status(stack, world):
    r = stack.get("/health", key=None)

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict) and isinstance(body.get("status"), str) and body["status"]


def test_health_stays_200_while_a_backend_is_down(factory):
    """Container health checks must not restart the router because a model server is away."""
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack([route("Solo", "chat", backend, models=["solo"])])
    backend.stop()

    assert stack.get("/health", key=None).status_code == 200


# ---- C8 --------------------------------------------------------------------


@pytest.mark.legacy_only
def test_health_lists_backend_urls_and_key_count_today(stack, world):
    body = stack.get("/health", key=None).json()

    assert body["status"] == "healthy"
    assert body["router"] == "model-orchestrator"
    assert body["database"]["status"] == "healthy"
    assert body["database"]["keys_loaded"] >= 1
    assert body["backends"] == {
        world.chat.url: "healthy",
        world.reasoner.url: "healthy",
        world.vision.url: "healthy",
        world.embed.url: "healthy",
        world.rerank.url: "healthy",
        world.whisper.url: "skipped",  # health_path: null
        world.kokoro.url: "skipped",
    }


@pytest.mark.legacy_only
def test_health_probes_every_backend_on_every_call_today(stack, world):
    world.chat.health_status = 503
    for _ in range(3):
        body = stack.get("/health", key=None).json()

    assert body["backends"][world.chat.url] == "unhealthy"
    assert len(world.chat.calls("/health")) == 3


@pytest.mark.change("C8")
def test_public_health_reports_status_version_database_and_routes(stack, world):
    r = stack.get("/health", key=None)

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("status"), str)
    assert isinstance(body.get("version"), str) and body["version"]
    assert "database" in body
    text = json.dumps(body)
    for name in w.ROUTE_TYPES:
        assert name in text, f"route {name} is not in the public health view"


@pytest.mark.change("C8")
def test_public_health_hides_backend_urls_and_key_counts(stack, world):
    body = stack.get("/health", key=None).json()

    text = json.dumps(body)
    for fake in world.fakes:
        assert fake.url not in text and str(fake.port) not in text
    assert "backends" not in body, "per-backend detail belongs on the internal port"
    assert not find_values(body, "keys_loaded")
    assert stack.key.token[:10] not in text


@pytest.mark.change("C8")
def test_public_health_does_not_probe_on_every_call(stack, world):
    for _ in range(25):
        assert stack.get("/health", key=None).status_code == 200

    # the background prober (1 s interval here) may tick once or twice meanwhile, not 25 times
    assert len(world.chat.calls("/health")) <= 5


@pytest.mark.feature("internal_health")
def test_detailed_health_is_on_the_internal_port(stack, world):
    """Assumes the detailed view is ``GET /health`` on ``ROUTER_METRICS_PORT`` (design 7.7)."""
    import httpx

    r = httpx.get(f"{stack.router.metrics_url}/health", timeout=5, trust_env=False)

    assert r.status_code == 200
    text = r.text
    for fake in (world.chat, world.reasoner, world.vision, world.embed, world.rerank):
        assert fake.url in text


# ---- C9 --------------------------------------------------------------------


@pytest.mark.change("C9")
def test_json_metrics_endpoint_is_gone_from_the_api_port(stack, world):
    stack.chat(w.CHAT_ALIAS)

    with_key, without_key = stack.get("/metrics"), stack.get("/metrics", key=None)

    assert with_key.status_code == 404
    assert without_key.status_code == 404
    assert "key_metrics" not in with_key.text


@pytest.mark.legacy_only
def test_json_metrics_show_every_owner_to_any_key_holder_today(stack, world):
    other = stack.new_key(owner="someone-else")
    stack.chat(w.CHAT_ALIAS, key=other)
    stack.chat(w.CHAT_ALIAS)

    r = stack.get("/metrics")  # asked with the default key, owner "e2e"

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"key_metrics", "model_metrics", "system_metrics"}
    assert {"e2e", "someone-else"} <= set(body["key_metrics"])
    assert body["model_metrics"][w.CHAT_ALIAS]["request_count"] >= 2
    no_key = stack.get("/metrics", key=None)
    assert no_key.status_code == 401
    assert error_of(no_key)["type"] == "authentication_error"


# ---- C10 -------------------------------------------------------------------


@pytest.mark.change("C10")
@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_are_off_by_default(stack, world, path):
    assert stack.get(path, key=None).status_code == 404
    assert stack.get(path).status_code == 404


@pytest.mark.legacy_only
@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_are_public_today(stack, world, path):
    assert stack.get(path, key=None).status_code == 200


def test_docs_are_served_when_asked_for(factory):
    """``ROUTER_EXPOSE_DOCS=1`` turns them on (the legacy router serves them regardless)."""
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack([route("Solo", "chat", backend, models=["solo"])], env={"ROUTER_EXPOSE_DOCS": "1"})

    for path in DOC_PATHS:
        assert stack.get(path, key=None).status_code == 200, path
    assert "/v1/chat/completions" in stack.get("/openapi.json", key=None).json()["paths"]
