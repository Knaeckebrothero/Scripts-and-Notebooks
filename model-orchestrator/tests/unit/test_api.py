"""The request pipeline, in process: order of checks, bodies, headers, open endpoints.

The app is the real one. Its database is a stand-in and its backends are an
``httpx.MockTransport``, so nothing here touches the network or Postgres.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from model_orchestrator.api import create_app
from model_orchestrator.auth import KeyRecord, hash_token
from model_orchestrator.config import parse_config
from model_orchestrator.db import Database, Migration
from model_orchestrator.settings import Settings
from model_orchestrator.state import RouterState
from model_orchestrator.usage import UsageBatch

CHAT_A, CHAT_B = "http://chat-a.invalid:8000", "http://chat-b.invalid:8000"
EMBED, RERANK, STT, TTS, DEAD = (
    "http://embed.invalid:8000",
    "http://rerank.invalid:8000",
    "http://stt.invalid:8000",
    "http://tts.invalid:8000",
    "http://dead.invalid:8000",
)

CONFIG = {
    "routes": [
        {
            "name": "Chat-Pool",
            "type": "chat",
            "backend": [CHAT_A, CHAT_B],
            "endpoint": "/v1/chat/completions",
            "backend_model_name": "org/chat-model",
            "models": ["chat", "gpt-4o-mini"],
            "owned_by": "acme",
            "request_defaults": {
                "skip_special_tokens": False,
                "chat_template_kwargs": {"enable_thinking": True},
            },
        },
        {
            "name": "Embed",
            "type": "embedding",
            "backend": EMBED,
            "endpoint": "/v1/embeddings",
            "backend_model_name": "org/embed",
            "models": ["embed"],
        },
        {
            "name": "Rerank",
            "type": "reranker",
            "backend": RERANK,
            "endpoint": "/v1/rerank",
            "models": ["rerank"],
        },
        {
            "name": "Whisper",
            "type": "stt",
            "backend": STT,
            "endpoint": "/v1/audio/transcriptions",
            "backend_model_name": "whisper-1",
            "health_path": None,
            "models": ["whisper", "whisper-large"],
        },
        {
            "name": "Kokoro",
            "type": "tts",
            "backend": TTS,
            "endpoint": "/v1/audio/speech",
            "backend_model_name": "kokoro",
            "health_path": None,
            "models": ["kokoro", "tts-1"],
        },
        {
            "name": "Dead",
            "type": "chat",
            "backend": DEAD,
            "endpoint": "/v1/chat/completions",
            "models": ["dead"],
        },
    ]
}

CHAT_OK = {
    "id": "c1",
    "choices": [{"message": {"content": "hi"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
}
SSE_BODY = (
    b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
    b"data: [DONE]\n\n"
)


def key(key_id: int = 1, **overrides: Any) -> KeyRecord:
    base: dict[str, Any] = {
        "id": key_id,
        "name": f"key-{key_id}",
        "owner": "alice",
        "key_prefix": "sk-test",
        "rate_limit_rpm": 100,
        "max_concurrency": None,
        "allowed_models": None,
        "enabled": True,
        "expires_at": None,
    }
    base.update(overrides)
    return KeyRecord(**base)


class FakeDatabase(Database):
    def __init__(self, keys: dict[str, KeyRecord]) -> None:
        super().__init__("postgresql://unused.invalid/none", migrations_dir=".")
        self.keys = keys
        self.batches: list[UsageBatch] = []
        self.synced: list[tuple[dict[str, str], bool]] = []

    async def open(self, *, timeout_s: float = 0) -> None:
        self.healthy = True

    async def close(self) -> None:
        return None

    async def migrate(self) -> list[Migration]:
        return []

    async def sync_routes(self, routes: Any, *, prune: bool) -> None:
        self.synced.append((dict(routes), prune))

    async def load_keys(self) -> dict[str, KeyRecord]:
        return {hash_token(token): record for token, record in self.keys.items()}

    async def write_usage(self, batch: UsageBatch, touch_key_ids: list[int]) -> None:
        self.batches.append(batch)

    async def ping(self, *, timeout_s: float = 2.0) -> bool:
        return self.healthy

    def outcomes(self) -> dict[tuple[int, str, str, str], int]:
        out: dict[tuple[int, str, str, str], int] = {}
        for batch in self.batches:
            for (_, key_id, model, route, outcome), row in batch.hourly.items():
                out[(key_id, model, route, outcome)] = (
                    out.get((key_id, model, route, outcome), 0) + row.requests
                )
        return out


class Backends:
    """All backends behind one mock transport. ``answer`` can be replaced per test."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answer: Callable[[httpx.Request], httpx.Response] = self.default
        # Set to an Event to make every backend call wait for it (a slow backend).
        self.hold: asyncio.Event | None = None

    def default(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/models":
            if request.url.host.startswith("chat-"):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "org/chat-model",
                                "object": "model",
                                "owned_by": "vllm",
                                "max_model_len": 8192,
                            }
                        ]
                    },
                )
            return httpx.Response(404, json={"detail": "Not Found"})
        if path == "/v1/chat/completions":
            if b'"stream":true' in request.content:
                return httpx.Response(200, content=SSE_BODY, headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json=CHAT_OK)
        if path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [], "usage": {"prompt_tokens": 5, "total_tokens": 5}})
        if path == "/v1/rerank":
            return httpx.Response(200, json={"results": []})
        if path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": "hello"})
        if path == "/v1/audio/speech":
            return httpx.Response(200, content=b"RIFFaudio", headers={"content-type": "audio/wav"})
        if path == "/v1/audio/voices":
            return httpx.Response(200, json={"voices": ["af_bella"]})
        return httpx.Response(404)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host.startswith("dead"):
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/health":
            return httpx.Response(200)
        self.requests.append(request)
        if self.hold is not None:
            await self.hold.wait()
        return self.answer(request)

    def to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]

    def last_json(self, path: str) -> dict[str, Any]:
        return json.loads(self.to(path)[-1].content)


class Harness:
    def __init__(
        self, client: httpx.AsyncClient, state: RouterState, db: FakeDatabase, backends: Backends
    ) -> None:
        self.client, self.state, self.db, self.backends = client, state, db, backends

    async def chat(self, token: str | None = "sk-main", body: Any = None, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        headers.update(kwargs.pop("headers", {}))
        payload = {"model": "chat", "messages": []} if body is None else body
        return await self.client.post("/v1/chat/completions", json=payload, headers=headers, **kwargs)

    async def flush(self) -> dict[tuple[int, str, str, str], int]:
        """Stop the router so every usage event is written, then return them."""
        await self.state.usage.stop(self.state._usage_task)
        self.state._usage_task = None
        return self.db.outcomes()


AUTH = {"Authorization": "Bearer sk-main"}


@asynccontextmanager
async def running(keys: dict[str, KeyRecord] | None = None, **settings: Any) -> AsyncIterator[Harness]:
    base: dict[str, Any] = {
        "database_url": "postgresql://unused.invalid/none",
        "metrics_port": 0,
        "config_poll_seconds": 0,
        "health_interval": 3600.0,
        "keys_refresh_seconds": 3600.0,
        "version": "9.9.9",
        "git_sha": "abc1234",
    }
    base.update(settings)
    db = FakeDatabase(keys if keys is not None else {"sk-main": key()})
    backends = Backends()
    app = create_app(
        Settings(**base),
        parse_config(CONFIG),
        database=db,
        transport_factory=lambda _: httpx.MockTransport(backends),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://router.test") as client:
            yield Harness(client, app.state.router, db, backends)


def error_of(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert set(body) == {"error"}, body
    assert {"message", "type"} <= set(body["error"]) <= {"message", "type", "code"}
    return body["error"]


# -- the happy path and what reaches the backend --------------------------------------------


async def test_chat_rewrites_the_model_and_merges_defaults() -> None:
    async with running() as h:
        response = await h.chat(
            body={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "chat_template_kwargs": {"reasoning_strength": "low"},
                "x_unknown": {"kept": True},
            }
        )
        assert response.status_code == 200 and response.json() == CHAT_OK
        sent = h.backends.last_json("/v1/chat/completions")
        assert sent == {
            "model": "org/chat-model",
            "messages": [{"role": "user", "content": "hi"}],
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_strength": "low"},
            "x_unknown": {"kept": True},
            "skip_special_tokens": False,
        }
        request = h.backends.to("/v1/chat/completions")[-1]
        assert "authorization" not in request.headers, "the client's key never reaches a backend"
        assert request.headers["x-request-id"] == response.headers["x-request-id"]
        assert request.headers["accept-encoding"] == "identity"


async def test_a_discovered_id_is_forwarded_as_sent() -> None:
    async with running() as h:
        response = await h.chat(body={"model": "org/chat-model", "messages": []})
        assert response.status_code == 200
        assert h.backends.last_json("/v1/chat/completions")["model"] == "org/chat-model"


async def test_stream_usage_is_requested_and_the_usage_event_withheld() -> None:
    async with running() as h:
        response = await h.chat(body={"model": "chat", "stream": True, "messages": []})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.content == b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
        assert h.backends.last_json("/v1/chat/completions")["stream_options"] == {"include_usage": True}
        assert response.headers["x-ratelimit-limit"] == "100"
        assert response.headers["x-request-id"]


async def test_stream_is_untouched_when_the_client_asked_for_usage() -> None:
    async with running() as h:
        body = {"model": "chat", "stream": True, "stream_options": {"include_usage": True}, "messages": []}
        response = await h.chat(body=body)
        assert response.content == SSE_BODY
        assert h.backends.last_json("/v1/chat/completions")["stream_options"] == {"include_usage": True}


async def test_embeddings_and_rerank() -> None:
    async with running() as h:
        embed = await h.client.post(
            "/v1/embeddings", json={"model": "embed", "input": "x", "stream": True}, headers=AUTH
        )
        assert embed.status_code == 200
        assert h.backends.last_json("/v1/embeddings") == {"model": "org/embed", "input": "x", "stream": True}
        assert (
            await h.client.post(
                "/v1/rerank", json={"model": "rerank", "query": "q", "top_n": 3}, headers=AUTH
            )
        ).status_code == 200
        assert h.backends.last_json("/v1/rerank") == {"model": "rerank", "query": "q", "top_n": 3}, (
            "C5: a number is forwarded"
        )
        await h.client.post("/v1/rerank", json={"model": "rerank", "query": "q", "top_n": None}, headers=AUTH)
        assert h.backends.last_json("/v1/rerank") == {"model": "rerank", "query": "q"}, (
            "C5: only null is dropped"
        )


async def test_speech_returns_the_backends_bytes_and_content_type() -> None:
    async with running() as h:
        response = await h.client.post(
            "/v1/audio/speech", json={"model": "tts-1", "input": "hi"}, headers=AUTH
        )
        assert response.status_code == 200 and response.content == b"RIFFaudio"
        assert response.headers["content-type"] == "audio/wav"
        assert h.backends.last_json("/v1/audio/speech") == {"model": "kokoro", "input": "hi"}


# -- order of checks (design 7.4) ----------------------------------------------------------------


async def test_auth_comes_first() -> None:
    async with running() as h:
        for response in (
            await h.client.post("/v1/chat/completions", content=b"{not json"),
            await h.chat(token="sk-wrong", body={"model": "nope"}),
            await h.client.post("/v1/chat/completions", json=[], headers={"Authorization": "Basic abc"}),
        ):
            assert response.status_code == 401
            assert error_of(response) == {
                "message": "Invalid, disabled, or expired API key",
                "type": "authentication_error",
            }
            assert response.headers["x-router-error-source"] == "router"
            assert "x-ratelimit-limit" not in response.headers


async def test_disabled_and_expired_keys_get_the_same_401() -> None:
    keys = {
        "sk-off": key(1, enabled=False),
        "sk-old": key(2, expires_at=datetime.now(UTC) - timedelta(minutes=1)),
        "sk-ok": key(3, expires_at=datetime.now(UTC) + timedelta(days=1)),
    }
    async with running(keys) as h:
        off, old, ok = await h.chat("sk-off"), await h.chat("sk-old"), await h.chat("sk-ok")
        assert off.status_code == old.status_code == 401 and off.json() == old.json()
        assert ok.status_code == 200


async def test_an_empty_key_table_denies_everything() -> None:
    async with running({}) as h:
        assert (await h.chat("sk-main")).status_code == 401
        assert (await h.client.get("/v1/models")).status_code == 200, "listing stays open"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"{not json", "Invalid JSON body"),
        (b"[]", "Request body must be a JSON object"),
        (b'"text"', "Request body must be a JSON object"),
        (b"null", "Request body must be a JSON object"),
    ],
)
async def test_bad_json_is_400(content: bytes, message: str) -> None:
    """C12: a body that is not an object used to be a plain-text 500."""
    async with running() as h:
        response = await h.client.post("/v1/chat/completions", content=content, headers=AUTH)
        assert response.status_code == 400
        assert error_of(response) == {"message": message, "type": "invalid_request_error"}
        assert response.headers["x-request-id"]


@pytest.mark.parametrize(
    ("body", "shown"),
    [({}, "None"), ({"model": None}, "None"), ({"model": "nope"}, "nope"), ({"model": ""}, "")],
)
async def test_unknown_model_is_404(body: dict[str, Any], shown: str) -> None:
    async with running() as h:
        response = await h.chat(body=body)
        assert response.status_code == 404
        assert error_of(response) == {
            "message": f"Model '{shown}' not found",
            "type": "invalid_request_error",
        }


@pytest.mark.parametrize("model", [5, 1.5, True, ["chat"], {"id": "chat"}], ids=repr)
async def test_a_model_that_is_not_a_string_is_400(model: Any) -> None:
    """A malformed request, not an unknown model (and no longer a plain-text 500 for a list)."""
    async with running({"sk-main": key(rate_limit_rpm=1)}) as h:
        response = await h.chat(body={"model": model, "messages": []}, headers={"X-Request-ID": "typed"})
        assert response.status_code == 400
        assert error_of(response) == {"message": "'model' must be a string", "type": "invalid_request_error"}
        assert response.headers["x-request-id"] == "typed"
        assert h.backends.to("/v1/chat/completions") == []
        assert (await h.chat()).status_code == 200, "it did not use the per-minute budget"


async def test_empty_body_is_an_unknown_model_as_before() -> None:
    async with running() as h:
        response = await h.client.post("/v1/chat/completions", headers=AUTH)
        assert response.status_code == 404 and error_of(response)["message"] == "Model 'None' not found"


@pytest.mark.parametrize(
    ("path", "model", "kind"),
    [
        ("/v1/chat/completions", "embed", "a chat model"),
        ("/v1/embeddings", "chat", "an embedding model"),
        ("/v1/rerank", "chat", "a reranker model"),
        ("/v1/audio/speech", "whisper", "a text-to-speech model"),
    ],
)
async def test_wrong_endpoint_for_the_model_is_400(path: str, model: str, kind: str) -> None:
    async with running() as h:
        response = await h.client.post(path, json={"model": model}, headers=AUTH)
        assert response.status_code == 400
        assert error_of(response)["message"] == f"Model '{model}' is not {kind}"


async def test_allowlist_holds_route_names_and_a_violation_is_403() -> None:
    async with running({"sk-main": key(allowed_models=frozenset({"Embed"}))}) as h:
        denied = await h.chat()
        assert denied.status_code == 403
        assert error_of(denied) == {
            "message": "API key is not allowed to use model route 'Chat-Pool'",
            "type": "permission_error",
        }
        allowed = await h.client.post("/v1/embeddings", json={"model": "embed", "input": "x"}, headers=AUTH)
        assert allowed.status_code == 200


async def test_404_wins_over_400_wins_over_403() -> None:
    async with running({"sk-main": key(allowed_models=frozenset({"Embed"}))}) as h:
        assert (
            await h.client.post("/v1/embeddings", json={"model": "nope"}, headers=AUTH)
        ).status_code == 404
        assert (
            await h.client.post("/v1/embeddings", json={"model": "chat"}, headers=AUTH)
        ).status_code == 400
        assert (
            await h.client.post("/v1/chat/completions", json={"model": "chat"}, headers=AUTH)
        ).status_code == 403


async def test_requests_rejected_before_the_limiter_do_not_use_the_budget() -> None:
    async with running({"sk-main": key(rate_limit_rpm=2, allowed_models=frozenset({"Chat-Pool"}))}) as h:
        for _ in range(5):
            await h.chat(body={"model": "nope"})
            await h.client.post("/v1/embeddings", json={"model": "chat"}, headers=AUTH)
            await h.client.post("/v1/embeddings", json={"model": "embed"}, headers=AUTH)
            await h.client.post("/v1/chat/completions", content=b"[]", headers=AUTH)
        first = await h.chat()
        assert first.status_code == 200 and first.headers["x-ratelimit-remaining"] == "1"


async def test_rate_limit_429_with_headers() -> None:
    async with running({"sk-main": key(rate_limit_rpm=2)}) as h:
        first, second, third = await h.chat(), await h.chat(), await h.chat()
        assert (first.status_code, second.status_code, third.status_code) == (200, 200, 429)
        assert [r.headers["x-ratelimit-remaining"] for r in (first, second, third)] == ["1", "0", "0"]
        assert error_of(third) == {"message": "Rate limit exceeded", "type": "rate_limit_error"}, (
            "no code on this one"
        )
        for response in (first, second, third):
            assert response.headers["x-ratelimit-limit"] == "2"
            assert int(response.headers["x-ratelimit-reset"]) > 1_700_000_000
        assert len(h.backends.to("/v1/chat/completions")) == 2
        assert (await h.flush())[(1, "chat", "Chat-Pool", "rate_limited")] == 1


async def test_unavailable_model_is_503_after_the_limiter() -> None:
    """C7: a known model whose backends are all down is not forwarded."""
    async with running({"sk-main": key(rate_limit_rpm=5)}) as h:
        response = await h.chat(body={"model": "dead", "messages": []})
        assert response.status_code == 503
        assert error_of(response) == {
            "message": "Model 'dead' is temporarily unavailable",
            "type": "api_error",
            "code": "model_unavailable",
        }
        assert response.headers["retry-after"] == "30"
        assert response.headers["x-router-error-source"] == "router"
        assert response.headers["x-ratelimit-remaining"] == "4", "C19: limit headers once the limiter has run"
        assert h.state.concurrency.in_flight(1) == 0, "the slot is released"
        assert (await h.flush()) == {(1, "dead", "Dead", "unavailable"): 1}


async def test_health_gating_off_forwards_to_a_backend_that_probes_as_down() -> None:
    async with running(health_gating=False) as h:
        response = await h.chat(body={"model": "dead", "messages": []})
        assert response.status_code == 503 and error_of(response)["code"] == "backend_unreachable"
        assert "dead" in [m["id"] for m in (await h.client.get("/v1/models")).json()["data"]]
        assert (await h.client.get("/health")).json()["routes"]["Dead"] == "down", "still reported"


async def test_concurrency_cap_is_429_with_code_and_retry_after() -> None:
    """C13. Exercised with a slot taken by hand: an in-process client cannot overlap requests."""
    async with running({"sk-main": key(max_concurrency=1), "sk-free": key(2)}) as h:
        assert h.state.concurrency.try_acquire(1, 1)
        response = await h.chat()
        assert response.status_code == 429
        error = error_of(response)
        assert error["code"] == "concurrency_limit_exceeded" and error["type"] == "rate_limit_error"
        assert response.headers["retry-after"] == "1"
        assert response.headers["x-ratelimit-remaining"] == "99", "the per-minute limiter ran first"
        assert (await h.chat("sk-free")).status_code == 200, "keys without a cap are unaffected"
        h.state.concurrency.release(1)
        assert (await h.chat()).status_code == 200
        assert h.state.concurrency.in_flight(1) == 0
        outcomes = await h.flush()
        assert outcomes[(1, "chat", "Chat-Pool", "concurrency_limited")] == 1


async def test_default_concurrency_cap_applies_to_keys_without_one() -> None:
    """ROUTER_DEFAULT_MAX_CONCURRENCY: off by default, a knob for the operator."""
    keys = {"sk-main": key(1), "sk-own": key(2, max_concurrency=3)}
    async with running(keys, default_max_concurrency=1) as h:
        assert h.state.concurrency.try_acquire(1, None)
        capped = await h.chat("sk-main")
        assert capped.status_code == 429
        error = error_of(capped)
        assert error["code"] == "concurrency_limit_exceeded" and "(limit 1)" in error["message"]
        assert capped.headers["retry-after"] == "1"
        h.state.concurrency.release(1)
        assert (await h.chat("sk-main")).status_code == 200
        # A key with its own cap keeps it, whatever the default says.
        assert h.state.concurrency.try_acquire(2, None)
        assert h.state.concurrency.try_acquire(2, None)
        assert (await h.chat("sk-own")).status_code == 200
        assert h.state.concurrency.try_acquire(2, None)
        assert (await h.chat("sk-own")).status_code == 429


async def test_without_a_default_cap_a_key_without_cap_is_unlimited() -> None:
    async with running() as h:
        assert h.state.settings.default_max_concurrency == 0
        for _ in range(50):
            assert h.state.concurrency.try_acquire(1, None)
        assert (await h.chat()).status_code == 200


async def test_no_free_upstream_connection_is_503_router_busy() -> None:
    async with running() as h:

        def pool_is_full(request: httpx.Request) -> httpx.Response:
            raise httpx.PoolTimeout("no connection available", request=request)

        h.backends.answer = pool_is_full
        response = await h.chat()
        assert response.status_code == 503
        assert error_of(response) == {
            "message": "The router has no free connection for this request right now",
            "type": "api_error",
            "code": "router_busy",
        }
        assert response.headers["retry-after"] == "1"
        assert response.headers["x-router-error-source"] == "router"
        assert len(h.backends.to("/v1/chat/completions")) == 1, "the other backend shares the pool: no retry"
        for backend in (CHAT_A, CHAT_B):
            assert h.state.health.passive_remaining(backend) == 0, "no backend is blamed"
        assert h.state.concurrency.in_flight(1) == 0
        assert h.state.metrics.rejections.labels("busy")._value.get() == 1
        assert (await h.flush()) == {(1, "chat", "Chat-Pool", "unreachable"): 1}


async def test_the_slot_is_released_after_errors_too() -> None:
    async with running({"sk-main": key(max_concurrency=1)}) as h:
        h.backends.answer = lambda request: httpx.Response(500, json={"error": {"message": "boom"}})
        assert (await h.chat()).status_code == 500
        assert h.state.concurrency.in_flight(1) == 0
        h.backends.answer = h.backends.default
        assert (await h.chat(body={"model": "chat", "stream": True, "messages": []})).status_code == 200
        assert h.state.concurrency.in_flight(1) == 0


# -- body size (C18) -------------------------------------------------------------------------------


async def test_body_over_the_limit_is_413() -> None:
    async with running(max_body_mb=0.001) as h:  # about 1 KiB
        big = json.dumps({"model": "chat", "messages": [{"role": "user", "content": "x" * 5000}]}).encode()
        declared = await h.client.post("/v1/chat/completions", content=big, headers=AUTH)

        async def chunks() -> AsyncIterator[bytes]:
            for start in range(0, len(big), 500):
                yield big[start : start + 500]

        streamed = await h.client.post("/v1/chat/completions", content=chunks(), headers=AUTH)
        for response in (declared, streamed):
            assert response.status_code == 413
            assert error_of(response)["type"] == "invalid_request_error"
        assert (await h.chat()).status_code == 200, "a small body still passes"
        assert (await h.client.post("/v1/chat/completions", content=big)).status_code == 401, (
            "auth is checked first"
        )
        assert h.backends.to("/v1/chat/completions")[0].content != big


async def test_multipart_over_the_limit_is_413() -> None:
    async with running(max_body_mb=0.001) as h:
        files = {"file": ("a.wav", b"x" * 5000, "audio/wav")}
        response = await h.client.post(
            "/v1/audio/transcriptions", files=files, data={"model": "whisper"}, headers=AUTH
        )
        assert response.status_code == 413


# -- speech-to-text (C6) ------------------------------------------------------------------------------


def parts_of(request: httpx.Request) -> list[tuple[str, str | None, bytes]]:
    """(name, filename, content) of each multipart part, in order."""
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    out = []
    for raw in request.content.split(b"--" + boundary)[1:-1]:
        head, _, content = raw.strip(b"\r\n").partition(b"\r\n\r\n")
        disposition = head.split(b"\r\n")[0].decode()
        name = disposition.split('name="')[1].split('"')[0]
        filename = disposition.split('filename="')[1].split('"')[0] if "filename=" in disposition else None
        out.append((name, filename, content))
    return out


async def test_stt_forwards_every_field_and_rewrites_only_the_model() -> None:
    async with running() as h:
        files = [
            ("file", ("speech.mp3", b"ID3audio", "audio/mpeg")),
            ("model", (None, "whisper-large")),
            ("language", (None, "de")),
            ("timestamp_granularities[]", (None, "word")),
            ("timestamp_granularities[]", (None, "segment")),
            ("prompt", (None, "Grüße")),
        ]
        response = await h.client.post("/v1/audio/transcriptions", files=files, headers=AUTH)
        assert response.status_code == 200 and response.json() == {"text": "hello"}
        request = h.backends.to("/v1/audio/transcriptions")[-1]
        assert parts_of(request) == [
            ("file", "speech.mp3", b"ID3audio"),
            ("model", None, b"whisper-1"),
            ("language", None, b"de"),
            ("timestamp_granularities[]", None, b"word"),
            ("timestamp_granularities[]", None, b"segment"),
            ("prompt", None, "Grüße".encode()),
        ]
        assert b"Content-Type: audio/mpeg" in request.content


async def test_stt_without_model_uses_the_first_stt_route() -> None:
    async with running() as h:
        response = await h.client.post(
            "/v1/audio/transcriptions", files={"file": ("a.wav", b"RIFF", "audio/wav")}, headers=AUTH
        )
        assert response.status_code == 200
        assert parts_of(h.backends.to("/v1/audio/transcriptions")[-1]) == [
            ("file", "a.wav", b"RIFF"),
            ("model", None, b"whisper-1"),
        ]
        assert (await h.flush()) == {(1, "whisper", "Whisper", "ok"): 1}


async def test_stt_without_file_is_400() -> None:
    async with running() as h:
        no_file = await h.client.post(
            "/v1/audio/transcriptions", data={"model": "whisper"}, files={"other": ("a", b"x")}, headers=AUTH
        )
        text_file = await h.client.post(
            "/v1/audio/transcriptions",
            files={"file": (None, "not an upload"), "model": (None, "whisper")},
            headers=AUTH,
        )
        not_multipart = await h.client.post(
            "/v1/audio/transcriptions", json={"model": "whisper"}, headers=AUTH
        )
        for response in (no_file, text_file, not_multipart):
            assert response.status_code == 400, response.text
            assert error_of(response)["type"] == "invalid_request_error"
        assert h.backends.to("/v1/audio/transcriptions") == []


async def test_stt_model_checks() -> None:
    async with running() as h:
        upload = {"file": ("a.wav", b"RIFF", "audio/wav")}
        unknown = await h.client.post(
            "/v1/audio/transcriptions", files=upload, data={"model": "nope"}, headers=AUTH
        )
        wrong = await h.client.post(
            "/v1/audio/transcriptions", files=upload, data={"model": "chat"}, headers=AUTH
        )
        anonymous = await h.client.post("/v1/audio/transcriptions", files=upload, data={"model": "whisper"})
        assert (unknown.status_code, wrong.status_code, anonymous.status_code) == (404, 400, 401)
        assert error_of(wrong)["message"] == "Model 'chat' is not a speech-to-text model"


# -- voices (C15) ----------------------------------------------------------------------------------------


async def test_voices_needs_no_auth_and_is_cached_per_route() -> None:
    async with running() as h:
        first = await h.client.get("/v1/audio/voices")
        second = await h.client.get("/v1/audio/voices", params={"model": "tts-1"})
        third = await h.client.get("/v1/audio/voices", params={"model": "kokoro"})
        for response in (first, second, third):
            assert response.status_code == 200 and response.json() == {"voices": ["af_bella"]}
            assert "x-ratelimit-limit" not in response.headers
        assert len(h.backends.to("/v1/audio/voices")) == 1, "one backend call for the whole route"
        assert str(h.backends.to("/v1/audio/voices")[0].url) == f"{TTS}/v1/audio/voices"
        assert (await h.flush()) == {}, "no key, no usage row"


async def test_voices_errors() -> None:
    async with running() as h:
        unknown = await h.client.get("/v1/audio/voices", params={"model": "nope"})
        wrong = await h.client.get("/v1/audio/voices", params={"model": "chat"})
        assert unknown.status_code == 404 and error_of(unknown)["message"] == "Model 'nope' not found"
        assert (
            wrong.status_code == 400
            and error_of(wrong)["message"] == "Model 'chat' is not a text-to-speech model"
        )


async def test_a_failed_voices_call_is_remembered_for_a_few_seconds() -> None:
    """The endpoint needs no key: a failing backend must not be asked again for every caller."""
    async with running() as h:
        assert h.state.voices.failure_ttl_s == 5.0
        h.state.voices.failure_ttl_s = 0.15
        h.backends.answer = lambda request: httpx.Response(500, json={"detail": "starting"})
        answers = [await h.client.get("/v1/audio/voices") for _ in range(10)]
        assert {a.status_code for a in answers} == {500}
        assert {a.json()["error"]["message"] for a in answers} == {"starting"}
        assert all(a.headers["x-router-error-source"] == "backend" for a in answers)
        assert len(h.backends.to("/v1/audio/voices")) == 1, "one backend call, ten callers"
        # The failure is not kept for long: once it has expired the backend is asked again.
        h.backends.answer = h.backends.default
        await asyncio.sleep(0.2)
        assert (await h.client.get("/v1/audio/voices")).status_code == 200
        assert len(h.backends.to("/v1/audio/voices")) == 2


async def test_concurrent_voices_callers_wait_for_one_fetch() -> None:
    async with running() as h:
        h.backends.hold = asyncio.Event()
        callers = [asyncio.create_task(h.client.get("/v1/audio/voices")) for _ in range(25)]
        for _ in range(200):
            if h.backends.to("/v1/audio/voices"):
                break
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.05)
        assert len(h.backends.to("/v1/audio/voices")) == 1, "25 callers, one backend call in flight"
        assert not any(c.done() for c in callers)
        h.backends.hold.set()
        answers = await asyncio.gather(*callers)
        assert all(a.status_code == 200 and a.json() == {"voices": ["af_bella"]} for a in answers)
        assert len(h.backends.to("/v1/audio/voices")) == 1
        assert h.state._voices_fetches == {}


async def test_a_voices_caller_that_gives_up_does_not_cancel_the_shared_fetch() -> None:
    async with running() as h:
        h.backends.hold = asyncio.Event()
        first = asyncio.create_task(h.client.get("/v1/audio/voices"))
        second = asyncio.create_task(h.client.get("/v1/audio/voices"))
        for _ in range(200):
            if h.backends.to("/v1/audio/voices"):
                break
            await asyncio.sleep(0.005)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        h.backends.hold.set()
        assert (await second).status_code == 200
        assert len(h.backends.to("/v1/audio/voices")) == 1


# -- models, health, public surface --------------------------------------------------------------------------


async def test_models_lists_available_ids_with_x_router() -> None:
    async with running() as h:
        response = await h.client.get("/v1/models")
        assert response.status_code == 200 and response.json()["object"] == "list"
        data = {m["id"]: m for m in response.json()["data"]}
        assert list(data) == [
            "chat",
            "gpt-4o-mini",
            "org/chat-model",
            "embed",
            "rerank",
            "whisper",
            "whisper-large",
            "kokoro",
            "tts-1",
        ]
        assert "dead" not in data, "C7: a model whose backends are all down is not listed"
        assert data["chat"] == {
            "id": "chat",
            "object": "model",
            "owned_by": "acme",
            "max_model_len": 8192,
            "x_router": {"route": "Chat-Pool", "type": "chat"},
        }
        assert data["embed"]["x_router"] == {"route": "Embed", "type": "embedding"}
        assert data["embed"]["owned_by"] == "local" and data["embed"]["created"] == 1704067200


async def test_get_model_including_ids_with_slashes() -> None:
    """C20."""
    async with running() as h:
        slash = await h.client.get("/v1/models/org/chat-model")
        assert slash.status_code == 200 and slash.json()["id"] == "org/chat-model"
        assert (await h.client.get("/v1/models/chat")).json()["x_router"] == {
            "route": "Chat-Pool",
            "type": "chat",
        }
        unknown = await h.client.get("/v1/models/org/nope")
        assert unknown.status_code == 404 and error_of(unknown)["message"] == "Model not found"
        down = await h.client.get("/v1/models/dead")
        assert down.status_code == 503 and error_of(down)["code"] == "model_unavailable"
        assert down.headers["retry-after"] == "30"


async def test_models_with_a_trailing_slash_redirects_to_the_listing_as_before() -> None:
    async with running() as h:
        response = await h.client.get("/v1/models/")
        assert response.status_code == 307
        assert response.headers["location"] == "http://router.test/v1/models"
        assert (await h.client.get("/v1/models/", follow_redirects=True)).json()["object"] == "list"


async def test_public_health_has_exactly_the_documented_shape() -> None:
    """C8: no backend URLs, no key counts."""
    async with running() as h:
        response = await h.client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "healthy",
            "router": "model-orchestrator",
            "version": "9.9.9",
            "database": {"status": "healthy"},
            "routes": {
                "Chat-Pool": "up",
                "Embed": "up",
                "Rerank": "up",
                "Whisper": "up",
                "Kokoro": "up",
                "Dead": "down",
            },
        }
        assert "invalid" not in response.text
        h.db.healthy = False
        degraded = (await h.client.get("/health")).json()
        assert degraded["status"] == "degraded" and degraded["database"] == {"status": "unavailable"}
        assert (await h.chat()).status_code == 200, "requests keep authenticating from the cached keys"


async def test_livez() -> None:
    async with running() as h:
        response = await h.client.get("/livez")
        assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_request_id_is_echoed_or_generated_on_every_response() -> None:
    async with running() as h:
        echoed = await h.chat(headers={"X-Request-ID": "client-chose-this"})
        assert echoed.headers["x-request-id"] == "client-chose-this"
        ids = set()
        for response in (
            await h.chat(),
            await h.chat(token=None),
            await h.client.get("/v1/models"),
            await h.client.get("/nope"),
            await h.client.post("/v1/chat/completions", content=b"[]", headers=AUTH),
        ):
            assert len(response.headers["x-request-id"]) == 12
            ids.add(response.headers["x-request-id"])
        assert len(ids) == 5


@pytest.mark.parametrize(
    ("method", "path", "status", "message"),
    [
        ("GET", "/nope", 404, "Not Found"),
        ("GET", "/metrics", 404, "Not Found"),
        ("GET", "/docs", 404, "Not Found"),
        ("GET", "/redoc", 404, "Not Found"),
        ("GET", "/openapi.json", 404, "Not Found"),
        ("GET", "/v1/chat/completions", 405, "Method Not Allowed"),
        ("DELETE", "/v1/models", 405, "Method Not Allowed"),
        ("POST", "/health", 405, "Method Not Allowed"),
    ],
)
async def test_unknown_paths_and_methods_use_the_envelope(
    method: str, path: str, status: int, message: str
) -> None:
    """C9, C10, C12."""
    async with running() as h:
        response = await h.client.request(method, path, headers=AUTH)
        assert response.status_code == status
        assert error_of(response) == {"message": message, "type": "invalid_request_error"}
        assert response.headers["x-request-id"] and response.headers["x-router-error-source"] == "router"


async def test_docs_can_be_switched_on() -> None:
    async with running(expose_docs=True) as h:
        assert (await h.client.get("/docs")).status_code == 200
        spec = (await h.client.get("/openapi.json")).json()
        assert "/v1/chat/completions" in spec["paths"] and spec["info"]["version"] == "9.9.9"


async def test_an_unexpected_error_is_a_500_envelope_with_request_id() -> None:
    """C12."""
    async with running() as h:

        def explode(request: httpx.Request) -> httpx.Response:
            raise RuntimeError("a bug, not a backend failure")

        h.backends.answer = explode
        response = await h.chat(headers={"X-Request-ID": "trace-me"})
        assert response.status_code == 500
        assert error_of(response) == {"message": "Internal server error", "type": "api_error"}
        assert response.headers["x-request-id"] == "trace-me"
        assert "a bug" not in response.text
        assert h.state.concurrency.in_flight(1) == 0
        assert h.state.metrics.internal_errors._value.get() == 1


# -- shutdown -----------------------------------------------------------------------------------------------


async def in_flight(h: Harness, **kwargs: Any) -> asyncio.Task[httpx.Response]:
    """A chat request that is waiting for its backend."""
    h.backends.hold = asyncio.Event()
    task = asyncio.create_task(h.chat(**kwargs))
    for _ in range(200):
        if h.backends.to("/v1/chat/completions"):
            return task
        await asyncio.sleep(0.005)
    raise AssertionError("the request never reached the backend")


@pytest.mark.parametrize("stream", [False, True])
async def test_a_request_the_server_ends_at_shutdown_gets_503_and_is_recorded(stream: bool) -> None:
    """After the grace period the server cancels what is still running. The client
    gets an answer, and the request a usage row and a log line."""
    async with running({"sk-main": key(max_concurrency=1)}) as h:
        task = await in_flight(h, body={"model": "chat", "messages": [], "stream": stream})
        h.state.note_shutdown()  # what the server's signal handler does
        task.cancel()  # what the server does when the grace period is over
        response = await task
        assert response.status_code == 503
        assert error_of(response) == {
            "message": "The router is shutting down",
            "type": "api_error",
            "code": "router_shutting_down",
        }
        assert response.headers["x-router-error-source"] == "router" and response.headers["x-request-id"]
        assert h.state.concurrency.in_flight(1) == 0
        assert h.state._active_requests == 0
        assert (await h.flush()) == {(1, "chat", "Chat-Pool", "unavailable"): 1}


async def test_shutdown_is_noticed_even_when_the_app_only_hears_of_it_after_the_cancel() -> None:
    """A server without the signal hook cancels first and runs the lifespan shutdown next."""
    async with running() as h:
        task = await in_flight(h)
        task.cancel()
        await asyncio.sleep(0.01)
        h.state.shutting_down = True
        h.state._shutdown_started.set()  # what stop() does first
        response = await task
        assert response.status_code == 503 and error_of(response)["code"] == "router_shutting_down"
        h.state.shutting_down = False


async def test_a_cancellation_that_is_not_a_shutdown_stays_a_cancellation() -> None:
    async with running({"sk-main": key(max_concurrency=1)}) as h:
        task = await in_flight(h)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert h.state.concurrency.in_flight(1) == 0, "cleaned up all the same"
        assert h.state._active_requests == 0


async def test_stop_waits_for_requests_that_are_being_ended_but_not_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import model_orchestrator.state as state_module

    monkeypatch.setattr(state_module, "_STOP_WAIT_S", 0.2)
    async with running() as h:
        h.state.request_started()
        stopping = asyncio.create_task(h.state.stop())
        await asyncio.sleep(0.05)
        assert not stopping.done(), "a request is still being recorded"
        assert h.state.shutting_down
        h.state.request_finished()
        await asyncio.wait_for(stopping, 2)

    async with running() as h:
        h.state.request_started()  # one that never ends
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(h.state.stop(), 2)
        assert 0.15 <= asyncio.get_running_loop().time() - started < 1.5, "bounded wait"


# -- errors from backends, seen from outside -----------------------------------------------------------------


async def test_backend_error_headers_and_usage() -> None:
    async with running() as h:
        h.backends.answer = lambda request: httpx.Response(
            422, json={"error": {"message": "bad schema", "type": "BadRequestError"}}
        )
        response = await h.chat()
        assert response.status_code == 422
        assert error_of(response) == {"message": "bad schema", "type": "invalid_request_error"}
        assert response.headers["x-router-error-source"] == "backend"
        assert response.headers["x-ratelimit-remaining"] == "99"
        assert (await h.flush()) == {(1, "chat", "Chat-Pool", "client_error"): 1}


async def test_usage_events_carry_tokens_model_id_and_route_name() -> None:
    async with running() as h:
        await h.chat(body={"model": "gpt-4o-mini", "messages": []})
        await h.chat(body={"model": "chat", "stream": True, "messages": []})
        await h.client.post("/v1/embeddings", json={"model": "embed", "input": "x"}, headers=AUTH)
        await h.flush()
        hourly = {key[2:]: row for batch in h.db.batches for key, row in batch.hourly.items()}
        assert len(hourly) == 3, "one row per (model, route, outcome) here"
        non_stream = hourly[("gpt-4o-mini", "Chat-Pool", "ok")]
        stream = hourly[("chat", "Chat-Pool", "ok")]
        embed = hourly[("embed", "Embed", "ok")]
        assert (non_stream.prompt_tokens, non_stream.completion_tokens) == (11, 7)
        assert (stream.prompt_tokens, stream.completion_tokens) == (3, 2), (
            "read from the withheld usage event"
        )
        assert (embed.prompt_tokens, embed.completion_tokens) == (5, 0)
        daily: dict[tuple[int, str], int] = {}
        for batch in h.db.batches:
            for key_route, row in batch.daily.items():
                daily[key_route] = daily.get(key_route, 0) + row.requests
        assert daily == {(1, "Chat-Pool"): 2, (1, "Embed"): 1}, "usage_daily is keyed by route name"


async def test_routes_are_synced_at_start_with_pruning_when_there_is_no_upstream() -> None:
    async with running() as h:
        names, prune = h.db.synced[0]
        assert names == {
            "Chat-Pool": "chat",
            "Embed": "embedding",
            "Rerank": "reranker",
            "Whisper": "stt",
            "Kokoro": "tts",
            "Dead": "chat",
        }
        assert prune is True
