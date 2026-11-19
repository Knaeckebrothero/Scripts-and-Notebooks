"""Config reload and upstream-router mode, in process (design 7.2 and 7.9)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import yaml

from model_orchestrator.api import create_app
from model_orchestrator.config import load_config
from model_orchestrator.settings import Settings
from model_orchestrator.state import RouterState, StableChange, config_signature

from .test_api import FakeDatabase, key

LOCAL = "http://local.invalid:8000"
OTHER = "http://other.invalid:8000"
UPSTREAM = "http://site-a-router.invalid:8091"
AUTH = {"Authorization": "Bearer sk-main"}


def chat_route(
    name: str = "Chat", backend: str = LOCAL, models: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "name": name,
        "type": "chat",
        "backend": backend,
        "endpoint": "/v1/chat/completions",
        "models": models or ["chat"],
        **extra,
    }


class Net:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down: set[str] = set()
        self.models: dict[str, list[dict[str, Any]]] = {
            "local.invalid": [{"id": "org/local-model"}],
            "other.invalid": [{"id": "org/other-model"}],
            "site-a-router.invalid": [
                {"id": "gemma", "owned_by": "google", "x_router": {"route": "Gemma-Pool", "type": "chat"}},
                {"id": "kokoro", "x_router": {"route": "Kokoro-TTS", "type": "tts"}},
                {"id": "chat", "x_router": {"route": "Remote-Chat", "type": "chat"}},
            ],
        }

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.down:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path
        if path in ("/health", "/livez"):
            return httpx.Response(200)
        if path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": self.models[host]})
        self.requests.append(request)
        if path == "/v1/audio/voices":
            return httpx.Response(200, json={"voices": ["af_bella"]})
        return httpx.Response(
            200, json={"id": "c1", "choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 1}}
        )


@asynccontextmanager
async def running(
    tmp_path: Path, document: dict[str, Any], **env: str
) -> AsyncIterator[tuple[httpx.AsyncClient, RouterState, FakeDatabase, Net, Path]]:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(document))
    settings = Settings(
        config_path=str(path),
        database_url="postgresql://unused.invalid/none",
        metrics_port=0,
        config_poll_seconds=0,
        health_interval=3600.0,
        keys_refresh_seconds=3600.0,
    )
    db, net = FakeDatabase({"sk-main": key()}), Net()
    os.environ.update(env)
    try:
        app = create_app(
            settings,
            load_config(str(path)),
            database=db,
            transport_factory=lambda _: httpx.MockTransport(net),
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://router.test"
            ) as client:
                yield client, app.state.router, db, net, path
    finally:
        for name in env:
            os.environ.pop(name, None)


def rewrite(path: Path, document: dict[str, Any] | str) -> None:
    path.write_text(document if isinstance(document, str) else yaml.safe_dump(document))


async def chat(client: httpx.AsyncClient, model: str) -> httpx.Response:
    return await client.post("/v1/chat/completions", json={"model": model, "messages": []}, headers=AUTH)


# -- reload -------------------------------------------------------------------------------------


async def test_a_valid_reload_replaces_the_route_table(tmp_path: Path) -> None:
    async with running(tmp_path, {"routes": [chat_route(), chat_route("Old", OTHER, ["old"])]}) as (
        client,
        state,
        db,
        _net,
        path,
    ):
        assert (await chat(client, "old")).status_code == 200
        assert (await chat(client, "org/local-model")).status_code == 200
        rewrite(path, {"routes": [chat_route(models=["chat", "chat-v2"]), chat_route("New", OTHER, ["new"])]})
        assert await state.reload("test") is True
        assert (await chat(client, "chat-v2")).status_code == 200
        assert (await chat(client, "new")).status_code == 200
        assert (await chat(client, "old")).status_code == 404
        assert (await chat(client, "org/local-model")).status_code == 200, (
            "discovered ids of a route that stays are kept"
        )
        assert state.last_reload_error is None
        assert state.metrics.config_reloads.labels("ok")._value.get() == 1
        assert [m["id"] for m in (await client.get("/v1/models")).json()["data"]] == [
            "chat",
            "chat-v2",
            "org/local-model",
            "new",
            "org/other-model",
        ]
        assert state.registry.table.route_names() == {"Chat": "chat", "New": "chat"}


async def test_an_invalid_reload_keeps_the_running_config(tmp_path: Path) -> None:
    """C11 at reload: a duplicate alias is rejected, nothing changes."""
    async with running(tmp_path, {"routes": [chat_route()]}) as (client, state, _db, _net, path):
        before = state.registry.table
        broken_files: tuple[dict[str, Any] | str, ...] = (
            {"routes": [chat_route(), chat_route("Second", OTHER, ["chat"])]},
            {"routes": [chat_route(request_default={})]},
            "routes: [",
            {"routes": [{"name": "X", "type": "chat"}]},
            # A truncated or half-written file must not wipe the route table.
            "",
            {"routes": []},
            {"server": {"host": "127.0.0.1"}},
        )
        for broken in broken_files:
            rewrite(path, broken)
            assert await state.reload("test") is False
            assert state.registry.table is before
            assert state.last_reload_error
            assert (await chat(client, "chat")).status_code == 200
        assert "no routes and no upstreams" in (state.last_reload_error or "")
        assert state.metrics.config_reloads.labels("error")._value.get() == len(broken_files)
        assert state.metrics.config_reloads.labels("ok")._value.get() == 0
        path.unlink()
        assert await state.reload("test") is False and "cannot read" in (state.last_reload_error or "")
        rewrite(path, {"routes": [chat_route(models=["fixed"])]})
        assert await state.reload("test") is True and state.last_reload_error is None
        assert (await chat(client, "fixed")).status_code == 200


async def test_reload_keeps_probe_state_and_probes_new_backends(tmp_path: Path) -> None:
    async with running(tmp_path, {"routes": [chat_route()]}) as (client, state, _db, net, path):
        net.down.add("other.invalid")
        rewrite(path, {"routes": [chat_route(), chat_route("Other", OTHER, ["other"])]})
        assert await state.reload("test") is True
        health = (await client.get("/health")).json()
        assert health["routes"] == {"Chat": "up", "Other": "down"}, (
            "a new backend is probed at once, at face value"
        )
        assert (await chat(client, "other")).status_code == 503


async def test_an_empty_config_is_still_allowed_at_startup(tmp_path: Path) -> None:
    async with running(tmp_path, {}) as (client, state, _db, _net, _path):
        assert state.registry.table.entries == {}
        assert (await client.get("/v1/models")).json() == {"object": "list", "data": []}
        assert (await chat(client, "anything")).status_code == 404
        assert (await client.get("/health")).json()["routes"] == {}


def test_a_file_change_counts_once_it_is_the_same_on_two_polls() -> None:
    applied, writing, written, final = (1, 10, 7), (2, 3, 7), (3, 40, 7), (4, 55, 7)
    change = StableChange()
    assert not change.observe(applied, applied)
    assert not change.observe(writing, applied), "first sighting: wait"
    assert not change.observe(written, applied), "still changing: wait"
    assert not change.observe(final, applied)
    assert change.observe(final, applied), "unchanged for two polls: reload"
    assert not change.observe(final, final), "loaded: nothing to do"
    assert not change.observe(final, final)


def test_a_change_that_is_reverted_before_it_settles_is_ignored() -> None:
    applied, other = (1, 10, 7), (2, 20, 7)
    change = StableChange()
    assert not change.observe(other, applied)
    assert not change.observe(applied, applied)
    assert not change.observe(other, applied), "the count starts again"
    assert change.observe(other, applied)


def test_a_file_that_disappears_is_a_change_too() -> None:
    applied = (1, 10, 7)
    change = StableChange()
    assert not change.observe(None, applied)
    assert change.observe(None, applied)
    assert not change.observe(None, None), "the failed load was remembered: no retry until it changes"
    assert not change.observe(applied, None)
    assert change.observe(applied, None)


def test_config_signature_changes_when_the_file_does(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    assert config_signature(str(path)) is None
    path.write_text("routes: []\n")
    first = config_signature(str(path))
    assert first is not None and config_signature(str(path)) == first
    path.write_text("routes: []\nserver: {}\n")
    second = config_signature(str(path))
    assert second is not None and second != first
    replacement = tmp_path / "new.yaml"
    replacement.write_text("routes: []\nserver: {}\n")
    os.replace(replacement, path)  # how an editor or a ConfigMap update swaps the file
    assert config_signature(str(path)) != second


# -- upstream-router mode --------------------------------------------------------------------------


UPSTREAM_DOC = {
    "routes": [chat_route("Local-Chat", models=["chat"], request_defaults={"skip_special_tokens": False})],
    "upstreams": [{"name": "site-a", "base_url": UPSTREAM, "api_key_env": "TEST_SITE_A_KEY"}],
}


async def test_upstream_ids_are_routable_and_forwarded_unchanged(tmp_path: Path) -> None:
    async with running(tmp_path, UPSTREAM_DOC, TEST_SITE_A_KEY="upstream-secret") as (
        client,
        state,
        _db,
        net,
        _path,
    ):
        response = await client.post(
            "/v1/chat/completions", json={"model": "gemma", "messages": [], "top_k": 5}, headers=AUTH
        )
        assert response.status_code == 200
        (request,) = net.requests
        assert str(request.url) == f"{UPSTREAM}/v1/chat/completions", "the same path on the upstream"
        assert request.headers["authorization"] == "Bearer upstream-secret"
        assert json.loads(request.content) == {"model": "gemma", "messages": [], "top_k": 5}, (
            "no rewrite, no defaults"
        )
        assert state.registry.table.get("gemma").route_name == "Gemma-Pool"  # type: ignore[union-attr]


async def test_local_routes_win_over_upstream_ids(tmp_path: Path) -> None:
    async with running(tmp_path, UPSTREAM_DOC, TEST_SITE_A_KEY="k") as (client, _state, _db, net, _path):
        assert (await chat(client, "chat")).status_code == 200
        assert net.requests[-1].url.host == "local.invalid"
        assert json.loads(net.requests[-1].content)["skip_special_tokens"] is False
        models = {m["id"]: m["x_router"] for m in (await client.get("/v1/models")).json()["data"]}
        assert models == {
            "chat": {"route": "Local-Chat", "type": "chat"},
            "org/local-model": {"route": "Local-Chat", "type": "chat"},
            "gemma": {"route": "Gemma-Pool", "type": "chat"},
            "kokoro": {"route": "Kokoro-TTS", "type": "tts"},
        }


async def test_upstream_route_names_drive_allowlists_and_type_checks(tmp_path: Path) -> None:
    async with running(tmp_path, UPSTREAM_DOC, TEST_SITE_A_KEY="k") as (client, state, db, _net, _path):
        db.keys["sk-main"] = key(allowed_models=frozenset({"Gemma-Pool"}))
        state.keys.replace(await db.load_keys())
        assert (await chat(client, "gemma")).status_code == 200
        assert (await chat(client, "chat")).status_code == 403
        wrong = await client.post("/v1/embeddings", json={"model": "gemma", "input": "x"}, headers=AUTH)
        assert wrong.status_code == 400 and "not an embedding model" in wrong.json()["error"]["message"]


async def test_upstream_voices_are_forwarded_with_the_model(tmp_path: Path) -> None:
    async with running(tmp_path, UPSTREAM_DOC, TEST_SITE_A_KEY="k") as (client, _state, _db, net, _path):
        assert (await client.get("/v1/audio/voices")).json() == {"voices": ["af_bella"]}
        assert str(net.requests[-1].url) == f"{UPSTREAM}/v1/audio/voices?model=kokoro"
        assert (await client.get("/v1/audio/voices", params={"model": "kokoro"})).status_code == 200
        assert len(net.requests) == 1, "cached"


async def test_upstream_route_names_are_synced_without_pruning(tmp_path: Path) -> None:
    async with running(tmp_path, UPSTREAM_DOC, TEST_SITE_A_KEY="k") as (_client, state, db, _net, _path):
        names, prune = db.synced[0]
        assert names == {"Local-Chat": "chat"} and prune is False, "at start only local names are known"
        await state._sync_routes()
        names, prune = db.synced[-1]
        assert names == {"Local-Chat": "chat", "Gemma-Pool": "chat", "Kokoro-TTS": "tts"}
        assert prune is False, "nothing is deleted while an upstream is configured"


async def test_an_upstream_that_goes_down_leaves_its_ids_known(tmp_path: Path) -> None:
    async with running(tmp_path, UPSTREAM_DOC, TEST_SITE_A_KEY="k") as (client, state, _db, net, _path):
        assert (await client.get("/health")).json()["routes"] == {
            "Local-Chat": "up",
            "Gemma-Pool": "up",
            "Kokoro-TTS": "up",
        }
        net.down.add("site-a-router.invalid")
        for _ in range(3):
            await state.prober.probe_all()
        response = await chat(client, "gemma")
        assert response.status_code == 503 and response.json()["error"]["code"] == "model_unavailable"
        assert (await client.get("/health")).json()["routes"] == {
            "Local-Chat": "up",
            "Gemma-Pool": "down",
            "Kokoro-TTS": "down",
        }
        assert [m["id"] for m in (await client.get("/v1/models")).json()["data"]] == [
            "chat",
            "org/local-model",
        ]
        assert (await chat(client, "chat")).status_code == 200


async def test_an_id_the_upstream_stops_listing_is_503_not_404(tmp_path: Path) -> None:
    async with running(tmp_path, UPSTREAM_DOC, TEST_SITE_A_KEY="k") as (client, state, _db, net, _path):
        net.models["site-a-router.invalid"] = net.models["site-a-router.invalid"][1:]
        await state.discoverer.discover(state.registry.targets)
        gone = await chat(client, "gemma")
        assert gone.status_code == 503 and gone.json()["error"]["code"] == "model_unavailable"
        assert (await chat(client, "never-seen")).status_code == 404


async def test_an_upstream_that_is_down_at_start_contributes_nothing_yet(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(UPSTREAM_DOC))
    settings = Settings(
        config_path=str(path),
        database_url="postgresql://unused.invalid/none",
        metrics_port=0,
        config_poll_seconds=0,
        health_interval=3600.0,
        keys_refresh_seconds=3600.0,
    )
    db, net = FakeDatabase({"sk-main": key()}), Net()
    net.down.add("site-a-router.invalid")
    app = create_app(
        settings, load_config(str(path)), database=db, transport_factory=lambda _: httpx.MockTransport(net)
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://router.test"
        ) as client:
            assert (await chat(client, "gemma")).status_code == 404
            net.down.clear()
            state: RouterState = app.state.router
            await state.prober.probe_all()
            await state.discoverer.discover(state.registry.targets)
            assert (await chat(client, "gemma")).status_code == 200
