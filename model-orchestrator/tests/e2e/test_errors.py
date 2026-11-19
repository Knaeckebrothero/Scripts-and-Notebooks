"""Section 5: route type check, unknown models, the error envelope. C2, C4, C12, C19, C22."""

from __future__ import annotations

import pytest

from tests.harness import world as w
from tests.harness.stack import user
from tests.harness.util import error_of

CHAT = "/v1/chat/completions"
EMBED = "/v1/embeddings"
RERANK = "/v1/rerank"
STT = "/v1/audio/transcriptions"
TTS = "/v1/audio/speech"
JSON = {"Content-Type": "application/json"}
AUDIO = {"file": ("a.wav", b"RIFF0000", "audio/wav")}


def call(stack, path: str, model: str | None, **kwargs):
    """A well-formed request for ``path`` naming ``model`` (None = leave the field out)."""
    named = {} if model is None else {"model": model}
    if path == STT:
        return stack.post(path, data=named, files=AUDIO, **kwargs)
    bodies = {
        CHAT: {"messages": user("hi")},
        EMBED: {"input": "hi"},
        RERANK: {"query": "q", "documents": ["a"]},
        TTS: {"input": "hi"},
    }
    return stack.post(path, json={**bodies[path], **named}, **kwargs)


# every endpoint with one model of each *other* type
WRONG_TYPE = [
    (path, alias)
    for path, own in [(CHAT, {"chat", "vision"}), (EMBED, {"embedding"}), (RERANK, {"reranker"}), (STT, {"stt"}), (TTS, {"tts"})]
    for type_, alias in sorted(set(w.ROUTE_TYPES.values()))
    if type_ not in own
]  # fmt: skip


# ---- type check and unknown models ------------------------------------------


@pytest.mark.parametrize(
    ("path", "alias"), WRONG_TYPE, ids=[f"{p.split('/')[-1]}:{a}" for p, a in WRONG_TYPE]
)
def test_model_on_the_wrong_endpoint_is_400(stack, world, path, alias):
    r = call(stack, path, alias)

    assert r.status_code == 400
    assert error_of(r)["type"] == "invalid_request_error"
    assert not [c for fake in world.fakes for c in fake.calls() if c.method == "POST"]


def test_discovered_id_on_the_wrong_endpoint_is_400(stack, world):
    r = call(stack, EMBED, w.CHAT_BACKEND_MODEL)

    assert r.status_code == 400
    error_of(r)


@pytest.mark.parametrize("path", [CHAT, EMBED, RERANK, STT, TTS])
def test_unknown_model_is_404(stack, world, path):
    r = call(stack, path, "no-such-model")

    assert r.status_code == 404
    error = error_of(r)
    assert error["type"] == "invalid_request_error"
    assert "no-such-model" in error["message"]


@pytest.mark.parametrize("path", [CHAT, EMBED, RERANK, TTS])
def test_missing_model_is_404(stack, world, path):
    r = call(stack, path, None)

    assert r.status_code == 404
    error_of(r)


@pytest.mark.parametrize("model", ["", None, 7, 1.5, True], ids=repr)
def test_model_that_is_not_a_string_is_rejected(stack, world, model):
    r = stack.post(CHAT, json={"model": model, "messages": user("hi")})

    assert r.status_code in (400, 404)
    error_of(r)
    assert not world.chat.calls(CHAT)


NOT_STRINGS = {"number": 7, "list": ["chat-alpha"], "object": {"id": "chat-alpha"}}


@pytest.mark.change("C22")
@pytest.mark.parametrize("path", [CHAT, EMBED, RERANK, TTS])
@pytest.mark.parametrize("kind", NOT_STRINGS)
def test_model_that_is_not_a_string_is_400(stack, world, path, kind):
    r = call(stack, path, NOT_STRINGS[kind], headers={"X-Request-ID": "c22-model"})

    assert r.status_code == 400
    assert error_of(r)["type"] == "invalid_request_error"
    assert r.headers.get("x-request-id") == "c22-model"
    assert not [c for fake in world.fakes for c in fake.calls() if c.method == "POST"]


@pytest.mark.legacy_only
@pytest.mark.parametrize("kind", ["list", "object"])
def test_model_that_is_a_list_or_object_is_a_plain_500_today(stack, world, kind):
    """An unhashable ``model`` blows up the route lookup (C22, "Today")."""
    r = stack.post(CHAT, json={"model": NOT_STRINGS[kind], "messages": user("hi")})

    assert (r.status_code, r.text) == (500, "Internal Server Error")
    assert "x-request-id" not in r.headers


@pytest.mark.legacy_only
def test_model_that_is_a_number_is_an_enveloped_404_today(stack, world):
    """Not the plain 500 that C22's "Today" column names: a number is hashable, so the lookup just misses."""
    r = stack.post(CHAT, json={"model": 7, "messages": user("hi")})

    assert r.status_code == 404
    assert error_of(r) == {"message": "Model '7' not found", "type": "invalid_request_error"}
    assert r.headers.get("x-request-id")


@pytest.mark.parametrize("path", [CHAT, EMBED, RERANK, TTS])
def test_invalid_json_is_400(stack, world, path):
    r = stack.post(path, content=b'{"model": "chat-alpha", ', headers=JSON)

    assert r.status_code == 400
    assert error_of(r)["type"] == "invalid_request_error"


def test_model_names_are_case_sensitive(stack, world):
    assert stack.chat(w.CHAT_ALIAS.upper()).status_code == 404


# ---- the envelope ----------------------------------------------------------


def test_errors_use_the_openai_envelope_with_a_type_per_status(stack, world):
    restricted = stack.new_key(allowed=[w.EMBED_ROUTE], rpm=1)
    cases = {
        401: (stack.chat(w.CHAT_ALIAS, key=None), "authentication_error"),
        404: (stack.chat("no-such-model"), "invalid_request_error"),
        400: (stack.chat(w.EMBED_ALIAS), "invalid_request_error"),
        403: (stack.chat(w.CHAT_ALIAS, key=restricted), "permission_error"),
    }
    assert call(stack, EMBED, w.EMBED_ALIAS, key=restricted).status_code == 200
    cases[429] = (call(stack, EMBED, w.EMBED_ALIAS, key=restricted), "rate_limit_error")

    for status, (response, type_) in cases.items():
        assert response.status_code == status
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert set(body) == {"error"}
        assert error_of(response)["type"] == type_
        assert {"message", "type"} <= set(body["error"])


@pytest.mark.legacy_only
def test_error_envelopes_never_carry_a_code_today(stack, world):
    world.kokoro.stop()
    answers = [
        stack.chat(w.CHAT_ALIAS, key=None),
        stack.chat("no-such-model"),
        stack.chat(w.EMBED_ALIAS),
        stack.post(TTS, json={"model": w.TTS_ALIAS, "input": "x"}),  # backend unreachable
    ]

    assert [a.status_code for a in answers] == [401, 404, 400, 503]
    for answer in answers:
        assert set(answer.json()["error"]) == {"message", "type"}
    assert answers[3].json()["error"]["message"].startswith("All backends unavailable: ConnectError")


@pytest.mark.change("C19")
def test_unreachable_backend_error_carries_a_code(stack, world):
    """The speech route has no health probe, so the request is forwarded and the connect fails."""
    world.kokoro.stop()

    r = stack.post(TTS, json={"model": w.TTS_ALIAS, "input": "x"})

    assert r.status_code == 503
    error = error_of(r)
    assert error.get("code") == "backend_unreachable"
    assert error["type"] == "api_error"
    assert world.kokoro.url not in error["message"] and "127.0.0.1" not in error["message"]
    assert r.headers.get("x-router-error-source") == "router"


# ---- C2: backend 5xx --------------------------------------------------------


@pytest.mark.change("C2")
@pytest.mark.parametrize("status", [500, 502, 503])
def test_backend_5xx_keeps_its_status_and_message(stack, world, status):
    world.chat.fail(
        status, {"error": {"message": "CUDA out of memory", "type": "InternalServerError", "code": status}}
    )

    r = stack.chat(w.CHAT_ALIAS)

    assert r.status_code == status
    error = error_of(r)
    assert error["message"] == "CUDA out of memory"
    assert error["type"] == "api_error"
    assert r.headers.get("x-router-error-source") == "backend"


@pytest.mark.legacy_only
def test_backend_5xx_becomes_503_all_backends_unavailable_today(stack, world):
    raw = world.chat.fail(
        500, {"error": {"message": "CUDA out of memory", "type": "InternalServerError", "code": 500}}
    )

    r = stack.chat(w.CHAT_ALIAS)

    assert r.status_code == 503
    assert error_of(r) == {"message": f"All backends unavailable: 500 {raw.decode()}", "type": "api_error"}


# ---- C4: backend error bodies ------------------------------------------------

BACKEND_ERRORS = {
    "openai-error-object": ({"error": {"message": "max_tokens is too large", "type": "BadRequestError", "code": 400}}, "max_tokens is too large"),
    "top-level-message": ({"object": "error", "message": "batch size 65 > maximum allowed batch size 64", "code": 422, "type": "Validation"}, "batch size 65 > maximum allowed batch size 64"),
    "error-string": ({"error": "Input validation error: inputs cannot be empty", "error_type": "Validation"}, "Input validation error: inputs cannot be empty"),
    "detail-string": ({"detail": "Unsupported voice"}, "Unsupported voice"),
    "plain-text": ("upstream said no", "upstream said no"),
}  # fmt: skip


@pytest.mark.change("C4")
@pytest.mark.parametrize("shape", BACKEND_ERRORS)
def test_backend_4xx_message_is_extracted_from_the_backend_body(stack, world, shape):
    body, message = BACKEND_ERRORS[shape]
    world.chat.fail(422, body)

    r = stack.chat(w.CHAT_ALIAS)

    assert r.status_code == 422
    error = error_of(r)
    assert error["message"] == message
    assert error["type"] == "invalid_request_error"
    assert r.headers.get("x-router-error-source") == "backend"


@pytest.mark.change("C4")
def test_errors_the_router_raises_itself_say_so(stack, world):
    answers = [stack.chat("no-such-model"), stack.chat(w.CHAT_ALIAS, key=None), stack.chat(w.EMBED_ALIAS)]

    assert [a.status_code for a in answers] == [404, 401, 400]
    assert [a.headers.get("x-router-error-source") for a in answers] == ["router"] * 3


@pytest.mark.change("C4")
def test_successful_responses_have_no_error_source_header(stack, world):
    r = stack.chat(w.CHAT_ALIAS)

    assert r.status_code == 200
    assert r.headers.get("x-request-id")
    assert "x-router-error-source" not in r.headers
    # ... and the header exists at all (otherwise this test would pass on any router)
    assert stack.chat("no-such-model").headers.get("x-router-error-source") == "router"


@pytest.mark.legacy_only
@pytest.mark.parametrize("shape", ["openai-error-object", "plain-text"])
def test_backend_4xx_body_is_wrapped_as_a_string_today(stack, world, shape):
    raw = world.chat.fail(422, BACKEND_ERRORS[shape][0])

    r = stack.chat(w.CHAT_ALIAS)

    assert r.status_code == 422
    assert error_of(r) == {"message": f"Backend error: {raw.decode()}", "type": "invalid_request_error"}
    assert "x-router-error-source" not in r.headers


def test_backend_4xx_status_and_text_reach_the_client(stack, world):
    """True before and after C4: same status, and the backend's words are in the message."""
    for status in (400, 404, 409, 413, 422, 429):
        world.chat.fail(
            status, {"error": {"message": f"backend says {status}", "type": "X", "code": status}}, times=1
        )

        r = stack.chat(w.CHAT_ALIAS)

        assert r.status_code == status
        assert f"backend says {status}" in error_of(r)["message"]
    assert len(world.chat.calls(CHAT)) == 6  # one attempt each: a 4xx is never retried


# ---- C12: everything else is enveloped too -----------------------------------


@pytest.mark.change("C12")
@pytest.mark.parametrize(("method", "path", "status"), [
    ("GET", "/no/such/path", 404),
    ("POST", "/v1/completions", 404),
    ("GET", "/v1/chat/completions", 405),
    ("DELETE", "/v1/models", 405),
    ("POST", "/livez", 405),
])  # fmt: skip
def test_unknown_paths_and_wrong_methods_use_the_envelope(stack, world, method, path, status):
    r = stack.request(method, path, headers={"X-Request-ID": "c12-rid"})

    assert r.status_code == status
    assert error_of(r)["type"] == "invalid_request_error"
    assert r.headers.get("x-request-id") == "c12-rid"


@pytest.mark.legacy_only
def test_unknown_paths_and_wrong_methods_use_fastapi_detail_today(stack, world):
    not_found = stack.get("/no/such/path")
    wrong_method = stack.get(CHAT)

    assert (not_found.status_code, not_found.json()) == (404, {"detail": "Not Found"})
    assert (wrong_method.status_code, wrong_method.json()) == (405, {"detail": "Method Not Allowed"})


NOT_OBJECTS = [b"[]", b'["a"]', b'"text"', b"42", b"null", b"true"]


@pytest.mark.change("C12")
@pytest.mark.parametrize("path", [CHAT, EMBED, RERANK, TTS])
@pytest.mark.parametrize("body", NOT_OBJECTS, ids=lambda b: b.decode())
def test_json_body_that_is_not_an_object_is_400(stack, world, path, body):
    r = stack.post(path, content=body, headers={**JSON, "X-Request-ID": "c12-body"})

    assert r.status_code == 400
    assert error_of(r)["type"] == "invalid_request_error"
    assert r.headers.get("x-request-id") == "c12-body"


@pytest.mark.legacy_only
@pytest.mark.parametrize("body", [b"[]", b"null", b"42"], ids=lambda b: b.decode())
def test_json_body_that_is_not_an_object_is_a_plain_500_today(stack, world, body):
    r = stack.post(CHAT, content=body, headers=JSON)

    assert r.status_code == 500
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "Internal Server Error"
    assert "x-request-id" not in r.headers


# ---- C22: a backend that closes the connection without a complete answer --------
# (the pool side of C22, a second backend being tried, is in test_pools.py)

BROKEN = ["reset_connection", "drop"]  # no response byte at all / headers and half the body


@pytest.mark.change("C22")
@pytest.mark.parametrize("fault", BROKEN)
def test_backend_that_closes_the_connection_is_a_502_backend_disconnected(stack, world, fault):
    getattr(world.chat, fault)()

    r = stack.chat(w.CHAT_ALIAS, headers={"X-Request-ID": "c22-broken"})

    assert r.status_code == 502
    error = error_of(r)
    assert error.get("code") == "backend_disconnected"
    assert error["type"] == "api_error"
    assert world.chat.url not in error["message"]
    assert r.headers.get("x-router-error-source") == "router"
    assert r.headers.get("x-request-id") == "c22-broken"


@pytest.mark.legacy_only
@pytest.mark.parametrize("fault", BROKEN)
def test_backend_that_closes_the_connection_is_a_plain_500_today(stack, world, fault):
    getattr(world.chat, fault)()

    r = stack.chat(w.CHAT_ALIAS)

    assert (r.status_code, r.text) == (500, "Internal Server Error")
    assert r.headers["content-type"].startswith("text/plain")
    assert "x-request-id" not in r.headers
