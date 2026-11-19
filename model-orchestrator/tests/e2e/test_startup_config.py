"""Section 5 "Environment variables and the YAML route schema": listen address, port, schema. C11."""

from __future__ import annotations

import pytest

from tests.harness.router import RouterProcess
from tests.harness.stack import route
from tests.harness.util import can_connect, free_port, port_is_free, wait_until

CHAT = "/v1/chat/completions"
OTHER_LOOPBACK = "127.0.0.2"  # Linux routes all of 127.0.0.0/8 to loopback


@pytest.fixture
def backend(factory):
    return factory.fake("vllm", ["acme/solo"])


def solo(backend):
    return [route("Solo", "chat", backend, models=["solo"], backend_model_name="acme/solo")]


# ---- listen address ---------------------------------------------------------


def test_router_host_env_beats_server_host(factory, backend):
    stack = factory.stack(
        solo(backend), server={"host": OTHER_LOOPBACK, "port": 8090}
    )  # ROUTER_HOST=127.0.0.1

    assert stack.get("/livez", key=None).status_code == 200
    assert can_connect("127.0.0.1", stack.router.port)
    assert not can_connect(OTHER_LOOPBACK, stack.router.port)


def test_server_host_is_used_when_router_host_is_unset(factory, backend):
    stack = factory.stack(
        solo(backend),
        server={"host": OTHER_LOOPBACK, "port": 8090},
        env={"ROUTER_HOST": None},
        host=OTHER_LOOPBACK,
    )

    assert stack.chat("solo").status_code == 200
    assert not can_connect("127.0.0.1", stack.router.port)


def test_empty_router_host_counts_as_unset(factory, backend):
    stack = factory.stack(
        solo(backend),
        server={"host": OTHER_LOOPBACK, "port": 8090},
        env={"ROUTER_HOST": ""},
        host=OTHER_LOOPBACK,
    )

    assert stack.get("/livez", key=None).status_code == 200
    assert not can_connect("127.0.0.1", stack.router.port)


@pytest.mark.parametrize("server", [{}, {"port": 8090}], ids=["empty-server-block", "port-only"])
def test_default_listen_address_is_all_interfaces(factory, backend, server):
    stack = factory.stack(solo(backend), server=server, env={"ROUTER_HOST": None})

    # reachable on two different loopback addresses: it bound the wildcard address
    assert can_connect("127.0.0.1", stack.router.port)
    assert can_connect(OTHER_LOOPBACK, stack.router.port)


def test_config_without_a_server_block_loads(factory, backend):
    stack = factory.stack(solo(backend), start=False)
    stack.router.write_config({"routes": solo(backend)})
    stack.router.start()

    assert stack.chat("solo").status_code == 200


# ---- port -------------------------------------------------------------------


def test_server_port_in_the_config_is_ignored(factory, backend):
    decoy = free_port()
    stack = factory.stack(solo(backend), server={"host": "127.0.0.1", "port": decoy})

    assert stack.get("/livez", key=None).status_code == 200  # on ROUTER_PORT
    assert stack.router.port != decoy
    assert not can_connect("127.0.0.1", decoy)


def test_default_port_is_8090(factory, backend):
    decoy = free_port()
    stack = factory.stack(
        solo(backend),
        server={"host": "127.0.0.1", "port": decoy},
        env={"ROUTER_PORT": None},
        port=8090,
        start=False,
    )
    if port_is_free(8090):
        stack.router.start()
        assert stack.get("/livez", key=None).status_code == 200
        assert not can_connect("127.0.0.1", decoy)
        return
    # Something else owns 8090 on this machine. Never talk to it: only check that the
    # router went for that port (and not for server.port) and gave up on it.
    stack.router.start(wait=False)
    code = stack.router.wait_exit(timeout=20)
    assert code is not None and code != 0
    log = stack.router.log()
    assert "8090" in log
    assert not can_connect("127.0.0.1", decoy)


# ---- route schema -------------------------------------------------------------


def test_route_with_every_documented_key_loads(factory):
    """The full YAML route schema of today, in one route."""
    proxy = factory.forward_proxy()
    backend = factory.fake("vllm", ["acme/full"])
    full = {
        "name": "Full",
        "type": "chat",
        "backend": [backend.url],
        "endpoint": CHAT,
        "backend_model_name": "acme/full",
        "models": ["full", "full-latest"],
        "owned_by": "acme",
        "proxy": proxy.url,
        "health_path": "/health",
        "backend_api_key_env": "E2E_FULL_KEY",
        "request_defaults": {"temperature": 0.3, "chat_template_kwargs": {"enable_thinking": True}},
    }
    stack = factory.stack([full], env={"E2E_FULL_KEY": "full-backend-key"})

    r = stack.chat("full-latest")

    assert r.status_code == 200, r.text
    seen = backend.last(CHAT)
    assert seen.json["model"] == "acme/full" and seen.json["temperature"] == 0.3
    assert seen.headers["authorization"] == "Bearer full-backend-key"
    assert stack.models()["full"]["owned_by"] == "acme"
    assert proxy.calls(CHAT)


def test_route_with_only_the_required_keys_loads(factory, backend):
    stack = factory.stack([{"name": "Min", "type": "chat", "backend": backend.url, "endpoint": CHAT}])

    assert stack.chat("acme/solo").status_code == 200


@pytest.mark.parametrize("health_path", [None, "", "/custom/health"], ids=["null", "empty", "custom"])
def test_health_path_variants_load(factory, backend, health_path):
    stack = factory.stack([{**solo(backend)[0], "health_path": health_path}])

    assert stack.chat("solo").status_code == 200
    assert stack.get("/health", key=None).status_code == 200


def test_endpoint_from_the_config_is_the_path_called_on_the_backend(factory):
    """``endpoint`` is not derived from the type: a reranker may live at ``/rerank``."""
    backend = factory.fake("reranker", ["acme/rr"])
    stack = factory.stack([{"name": "RR", "type": "reranker", "backend": backend.url, "endpoint": "/rerank",
                            "models": ["rr"]}])  # fmt: skip

    r = stack.post("/v1/rerank", json={"model": "rr", "query": "q", "documents": ["a"]})

    assert r.status_code >= 400  # the fake has nothing at /rerank ...
    assert [c.path for c in backend.calls() if c.method == "POST"] == [
        "/rerank"
    ]  # ... but that is where it went


def test_missing_database_url_refuses_to_start(factory, backend):
    stack = factory.stack(solo(backend), env={"ROUTER_DATABASE_URL": None}, start=False)

    stack.router.start(wait=False)

    code = stack.router.wait_exit_or_live(timeout=15)
    assert code is not None and code != 0, "the router kept running without a database URL"
    assert "ROUTER_DATABASE_URL" in stack.router.log()


def test_missing_config_file_refuses_to_start(factory, backend):
    stack = factory.stack(solo(backend), env={"ROUTER_CONFIG": "/nonexistent/e2e/config.yaml"}, start=False)

    stack.router.start(wait=False)

    code = stack.router.wait_exit_or_live(timeout=15)
    assert code is not None and code != 0


# ---- C11 --------------------------------------------------------------------


def twins(factory):
    first = factory.fake("vllm", ["acme/first"], name="first")
    second = factory.fake("vllm", ["acme/second"], name="second")
    routes = [
        route("First", "chat", first, backend_model_name="acme/first", models=["twin", "first-only"]),
        route("Second", "chat", second, backend_model_name="acme/second", models=["second-only", "twin"]),
    ]
    return first, second, routes


@pytest.mark.change("C11")
def test_duplicate_alias_fails_the_start_with_a_clear_message(factory):
    _, _, routes = twins(factory)
    stack = factory.stack(routes, start=False)

    stack.router.start(wait=False)

    code = stack.router.wait_exit_or_live(timeout=15)
    assert code is not None, "the router started although two routes share the alias 'twin'"
    assert code != 0
    log = stack.router.log()
    assert "twin" in log and "First" in log and "Second" in log


@pytest.mark.legacy_only
def test_duplicate_alias_later_route_silently_wins_today(factory):
    first, second, routes = twins(factory)
    stack = factory.stack(routes)

    assert stack.chat("twin").status_code == 200
    assert second.last(CHAT).json["model"] == "acme/second"
    assert not first.calls(CHAT)


@pytest.mark.feature("config_validation")
@pytest.mark.parametrize(
    "problem", ["duplicate-route-name", "unknown-key", "unknown-type", "missing-endpoint"]
)
def test_invalid_config_fails_the_start(factory, backend, problem):
    """Design 7.2: the config is validated; these start (and misbehave quietly) on the legacy router."""
    good = route("Solo", "chat", backend, models=["solo"])
    routes = {
        "duplicate-route-name": [good, {**good, "models": ["other"]}],
        "unknown-key": [{**good, "request_default": {"temperature": 0}}],  # misspelt request_defaults
        "unknown-type": [{**good, "type": "chatt"}],
        "missing-endpoint": [{k: v for k, v in good.items() if k != "endpoint"}],
    }[problem]
    stack = factory.stack(routes, start=False)

    stack.router.start(wait=False)

    code = stack.router.wait_exit_or_live(timeout=15)
    assert code is not None and code != 0, f"the router started with a config that has: {problem}"
    assert "Solo" in stack.router.log()


def test_the_harness_reports_a_router_that_dies_at_start(factory, backend):
    """Not about the router: a failed start must surface with the log, not as a hang."""
    stack = factory.stack(solo(backend), env={"ROUTER_DATABASE_URL": None}, start=False)

    with pytest.raises(RuntimeError, match="exited with code"):
        stack.router.start(timeout=15)
    assert stack.router not in RouterProcess.live or not stack.router.running
    wait_until(lambda: not stack.router.running, timeout=2)
