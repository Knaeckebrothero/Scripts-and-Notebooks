"""Section 5 "Headers": ``X-Request-ID``, and whose credentials reach a backend."""

from __future__ import annotations

import pytest

from tests.harness import world as w
from tests.harness.stack import route, user

CHAT = "/v1/chat/completions"
AUDIO = {"file": ("a.wav", b"RIFF0000", "audio/wav")}


def responses(stack, world, headers=None):
    """One response of every kind the router produces on purpose."""
    restricted = stack.db.create_key(allowed=[w.EMBED_ROUTE], rpm=1)
    stack.sync_keys()
    kw = {"headers": headers} if headers else {}
    out = {
        "livez": stack.get("/livez", key=None, **kw),
        "health": stack.get("/health", key=None, **kw),
        "models": stack.get("/v1/models", key=None, **kw),
        "model": stack.get(f"/v1/models/{w.CHAT_ALIAS}", key=None, **kw),
        "model 404": stack.get("/v1/models/nope", key=None, **kw),
        "voices": stack.get("/v1/audio/voices", key=None, **kw),
        "chat": stack.chat(w.CHAT_ALIAS, **kw),
        "embeddings": stack.post("/v1/embeddings", json={"model": w.EMBED_ALIAS, "input": "x"}, **kw),
        "rerank": stack.post(
            "/v1/rerank", json={"model": w.RERANK_ALIAS, "query": "q", "documents": ["a"]}, **kw
        ),
        "transcription": stack.post(
            "/v1/audio/transcriptions", data={"model": w.STT_ALIAS}, files=AUDIO, **kw
        ),
        "speech": stack.post("/v1/audio/speech", json={"model": w.TTS_ALIAS, "input": "x"}, **kw),
        "401": stack.chat(w.CHAT_ALIAS, key=None, **kw),
        "404": stack.chat("no-such-model", **kw),
        "400 type": stack.chat(w.EMBED_ALIAS, **kw),
        "400 json": stack.post(CHAT, content=b"{", **kw),
        "403": stack.chat(w.CHAT_ALIAS, key=restricted, **kw),
        "429 (after one ok)": [
            stack.post("/v1/embeddings", key=restricted, json={"model": w.EMBED_ALIAS, "input": "x"}, **kw)
            for _ in range(2)
        ][1],
    }
    world.chat.fail(400, times=1)
    out["backend 400"] = stack.chat(w.CHAT_ALIAS, **kw)
    world.chat.fail(500, times=1)
    out["backend 500"] = stack.chat(w.CHAT_ALIAS, **kw)
    return out


def test_every_response_carries_a_request_id(stack, world):
    answers = responses(stack, world)

    assert answers["429 (after one ok)"].status_code == 429
    missing = [name for name, r in answers.items() if not r.headers.get("x-request-id")]
    assert not missing, f"no X-Request-ID on: {missing}"
    ids = [r.headers["x-request-id"] for r in answers.values()]
    assert len(set(ids)) == len(ids), "request ids repeat"


def test_a_client_supplied_request_id_is_echoed(stack, world):
    answers = responses(stack, world, headers={"X-Request-ID": "client-chose-this-1234"})

    wrong = {name: r.headers.get("x-request-id") for name, r in answers.items()
             if r.headers.get("x-request-id") != "client-chose-this-1234"}  # fmt: skip
    assert not wrong


def test_request_id_on_streams(stack, world):
    generated = stack.chat_stream(w.CHAT_ALIAS)
    echoed = stack.chat_stream(w.CHAT_ALIAS, headers={"x-request-id": "stream-id-9"})

    assert generated.headers.get("x-request-id")
    assert echoed.headers.get("x-request-id") == "stream-id-9"


@pytest.mark.feature("request_id_to_backend")
def test_request_id_is_sent_to_the_backend(stack, world):
    """Design 7.5: the id now travels on, so a backend log line can be matched to a request."""
    stack.chat(w.CHAT_ALIAS, headers={"X-Request-ID": "trace-me-42"})
    generated = stack.chat(w.CHAT_ALIAS)

    first, second = world.chat.calls(CHAT)
    assert first.headers.get("x-request-id") == "trace-me-42"
    assert second.headers.get("x-request-id") == generated.headers["x-request-id"]


# ---- credentials -----------------------------------------------------------


def test_the_clients_key_is_never_forwarded(stack, world):
    token = stack.key.token
    stack.chat(w.CHAT_ALIAS)
    stack.chat_stream(w.CHAT_ALIAS)
    stack.post("/v1/embeddings", json={"model": w.EMBED_ALIAS, "input": "x"})
    stack.post("/v1/rerank", json={"model": w.RERANK_ALIAS, "query": "q", "documents": ["a"]})
    stack.post("/v1/audio/transcriptions", data={"model": w.STT_ALIAS}, files=AUDIO)
    stack.post("/v1/audio/speech", json={"model": w.TTS_ALIAS, "input": "x"})

    forwarded = [call for fake in world.fakes for call in fake.calls() if call.method == "POST"]
    assert len(forwarded) == 6
    for call in forwarded:
        assert "authorization" not in call.headers, call.path
        assert token not in repr(call.headers) and token.encode() not in call.body, call.path


def test_other_client_headers_are_not_forwarded(stack, world):
    stack.chat(
        w.CHAT_ALIAS, headers={"Cookie": "session=secret", "X-Api-Key": "also-secret", "X-Custom": "c"}
    )

    seen = world.chat.last(CHAT).headers
    assert "cookie" not in seen and "x-api-key" not in seen


def test_a_routes_backend_key_is_sent_instead(factory):
    backend = factory.fake("vllm", ["acme/guarded"])
    embed = factory.fake("tei")
    stack = factory.stack(
        [
            route("Guarded", "chat", backend, models=["guarded"], backend_model_name="acme/guarded",
                  backend_api_key_env="E2E_BACKEND_KEY"),
            route("Open", "embedding", embed, models=["open-embed"]),
        ],
        env={"E2E_BACKEND_KEY": "backend-secret-123"},
    )  # fmt: skip

    assert stack.chat("guarded").status_code == 200
    assert stack.chat_stream("guarded").status == 200
    assert stack.post("/v1/embeddings", json={"model": "open-embed", "input": "x"}).status_code == 200

    chat_calls = backend.calls(CHAT)
    assert [c.headers.get("authorization") for c in chat_calls] == ["Bearer backend-secret-123"] * 2
    # discovery uses the backend key too, otherwise a guarded backend would list nothing
    assert {c.headers.get("authorization") for c in backend.calls("/v1/models")} == {
        "Bearer backend-secret-123"
    }
    assert "acme/guarded" in stack.models()
    # the key belongs to its route only
    assert "authorization" not in embed.last("/v1/embeddings").headers


def test_backend_key_env_that_is_unset_is_only_a_warning(factory):
    backend = factory.fake("vllm", ["acme/guarded"])
    stack = factory.stack(
        [route("Guarded", "chat", backend, models=["guarded"], backend_model_name="acme/guarded",
               backend_api_key_env="E2E_KEY_THAT_IS_NOT_SET")],
    )  # fmt: skip

    assert stack.chat("guarded").status_code == 200
    assert "authorization" not in backend.last(CHAT).headers
    assert "E2E_KEY_THAT_IS_NOT_SET" in stack.router.log()


def test_api_keys_do_not_end_up_in_the_router_log(stack, world):
    key = stack.new_key()
    stack.chat(w.CHAT_ALIAS, key=key)
    stack.chat("no-such-model", key=key)
    stack.chat(w.CHAT_ALIAS, key=key.token + "x")  # a wrong key must not be logged either
    world.chat.fail(500, times=1)
    stack.chat(w.CHAT_ALIAS, key=key)

    log = stack.router.log()
    assert key.token not in log
    assert key.token[6:] not in log  # not even without the sk-mo- prefix


def test_request_body_content_is_not_logged_at_info(stack, world):
    stack.chat(w.CHAT_ALIAS, "a-very-recognisable-prompt-7f3a9c")
    stack.post("/v1/embeddings", json={"model": w.EMBED_ALIAS, "input": "another-recognisable-input-55ab"})

    log = stack.router.log()
    assert "a-very-recognisable-prompt-7f3a9c" not in log
    assert "another-recognisable-input-55ab" not in log


def test_multipart_and_json_content_types_reach_the_backend(stack, world):
    stack.chat(w.CHAT_ALIAS)
    stack.post("/v1/audio/transcriptions", data={"model": w.STT_ALIAS}, files=AUDIO)

    assert world.chat.last(CHAT).headers["content-type"].startswith("application/json")
    assert world.whisper.last().headers["content-type"].startswith("multipart/form-data; boundary=")
    assert world.chat.last(CHAT).json["messages"] == user("hello")
