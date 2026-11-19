"""Section 5 "Streaming": SSE is relayed byte for byte. Plus C1 (errors on streams) and C16 (cancellation)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.fakes import FakeVLLM
from tests.harness import world as w
from tests.harness.stack import route, user
from tests.harness.util import error_of, sse_data, wait_until

CHAT = "/v1/chat/completions"


def expected_stream(model: str, text: str, **body) -> bytes:
    """What the backend sends for this client request (usage event only if the client asked)."""
    return b"".join(FakeVLLM.render_stream({"model": model, "messages": user(text), **body}))


def stream_error(result) -> dict:
    """Assert a streaming request was answered with a plain JSON error envelope."""
    assert result.error is None, f"transfer broke: {result.error!r}"
    assert result.headers.get("content-type", "").startswith("application/json"), (
        f"expected a JSON error, got {result.status} {result.headers.get('content-type')!r}: {result.raw[:200]!r}"
    )
    body = json.loads(result.raw)
    assert isinstance(body.get("error"), dict) and body["error"].get("message"), body
    return body["error"]


# ---- byte-exact relay ------------------------------------------------------


def test_stream_is_relayed_byte_for_byte(stack, world):
    world.chat.stream_gap = 0.01

    s = stack.chat_stream(w.CHAT_ALIAS, "héllo wörld")

    assert s.status == 200 and s.error is None
    assert s.headers["content-type"].startswith("text/event-stream")
    assert s.raw == expected_stream(w.CHAT_BACKEND_MODEL, "héllo wörld")
    assert s.raw.endswith(b"data: [DONE]\n\n")
    assert all(event.endswith(b"\n\n") for event in s.events)
    assert world.chat.last(CHAT).completed


def test_stream_sent_in_one_burst_is_relayed_byte_for_byte(stack, world):
    world.chat.stream_gap = 0.0

    s = stack.chat_stream(w.CHAT_ALIAS, "burst")

    assert s.error is None
    assert s.raw == expected_stream(w.CHAT_BACKEND_MODEL, "burst")


def test_stream_without_include_usage_has_no_usage_event(stack, world):
    s = stack.chat_stream(w.CHAT_ALIAS)

    assert not [d for d in s.data if isinstance(d, dict) and "usage" in d]
    assert not [d for d in s.data if isinstance(d, dict) and d.get("choices") == []]


def test_stream_with_include_usage_is_relayed_untouched(stack, world):
    options = {"stream_options": {"include_usage": True}}

    s = stack.chat_stream(w.CHAT_ALIAS, "count me", body=options)

    seen = world.chat.last(CHAT)
    assert seen.json["stream_options"] == {"include_usage": True}
    assert s.raw == seen.sent
    assert s.raw == expected_stream(w.CHAT_BACKEND_MODEL, "count me", **options)
    usage_events = [d for d in s.data if isinstance(d, dict) and d.get("choices") == []]
    assert len(usage_events) == 1
    assert usage_events[0]["usage"] == FakeVLLM.usage({"messages": user("count me")})
    assert s.data[-1] == "[DONE]"


def test_stream_reasoning_deltas_reach_the_client(stack, world):
    s = stack.chat_stream(w.CHAT_ALIAS, "think")

    deltas = [d["choices"][0]["delta"] for d in s.data if isinstance(d, dict) and d.get("choices")]
    assert [d["reasoning"] for d in deltas if "reasoning" in d] == ["Let me think", " about it. 思考"]
    assert "".join(d.get("content", "") for d in deltas) == "Echo: think ✓ grüße"
    assert not [d for d in deltas if "reasoning_content" in d]


def test_stream_request_body_is_forwarded_untouched_apart_from_model(stack, world):
    extra = {"temperature": 0.1, "x_unknown": {"a": [1, None]}, "stop": ["END"]}

    stack.chat_stream(w.CHAT_ALIAS, "body", body=extra)

    seen = dict(world.chat.last(CHAT).json)
    # The client sent no stream_options. The new router may add them to learn the token
    # counts (design 7.6); nothing else may differ.
    assert seen.pop("stream_options", None) in (None, {"include_usage": True})
    assert seen == {"model": w.CHAT_BACKEND_MODEL, "messages": user("body"), "stream": True, **extra}


def test_stream_on_vision_route(stack, world):
    s = stack.chat_stream(w.VISION_ALIAS, "look")

    assert s.status == 200 and s.error is None
    assert s.raw == expected_stream(w.VISION_BACKEND_MODEL, "look")


def test_stream_on_discovered_id(stack, world):
    s = stack.chat_stream(w.CHAT_DISCOVERED, "lora")

    assert s.raw == expected_stream(w.CHAT_DISCOVERED, "lora")
    assert world.chat.last(CHAT).json["model"] == w.CHAT_DISCOVERED


EVENT = b'data: {"id":"x","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"\xc3\xa4\xe2\x9c\x93"}}]}\n\n'
SCRIPTS = {
    # an event cut at every awkward place: mid-JSON, inside a UTF-8 character, between the two newlines
    "split-inside-events": [EVENT[:7], EVENT[7:90], EVENT[90:91], EVENT[91:-1], EVENT[-1:] + EVENT[:20], EVENT[20:],
                            b"data: [DONE]\n", b"\n"],
    "several-events-in-one-chunk": [EVENT * 5 + b"data: [DONE]\n\n"],
    "crlf-line-endings": [EVENT.replace(b"\n\n", b"\r\n\r\n"), b'data: {"choices":[{"delta":{"content":"b"}}]}\r\n',
                          b"\r\n", b"data: [DONE]\r\n\r\n"],
    "comments-and-other-fields": [b": keep-alive\n\n", b"event: message\nid: 7\nretry: 100\n" + EVENT,
                                  b"data: line one\ndata: line two\n\n", b": ping\n\n", b"data: [DONE]\n\n"],
    "no-done-marker": [EVENT, EVENT],
    "unterminated-last-event": [EVENT, b'data: {"choices":[{"delta":{"content":"tail"}}]}'],
    "single-bytes": [bytes([b]) for b in EVENT] + [b"data: [DONE]\n\n"],
}  # fmt: skip


@pytest.mark.parametrize("name", SCRIPTS)
def test_stream_chunking_does_not_change_the_bytes(stack, world, name):
    """Whatever the backend's write boundaries and SSE dialect, the client gets the same bytes."""
    world.chat.stream_script = SCRIPTS[name]
    world.chat.stream_gap = 0.003

    s = stack.chat_stream(w.CHAT_ALIAS)

    assert s.status == 200 and s.error is None
    assert s.raw == b"".join(SCRIPTS[name])


def test_stream_is_relayed_as_it_arrives_not_buffered(stack, world):
    world.chat.pause_stream_after(3)
    seen_while_paused: list[bytes] = []

    def on_chunk(result):
        if not seen_while_paused:
            # the backend is stuck after three events and has not finished: this is a live relay
            assert not world.chat.last(CHAT).completed
            seen_while_paused.append(result.raw)
            world.chat.resume_stream()

    s = stack.chat_stream(w.CHAT_ALIAS, "live", timeout=5, on_chunk=on_chunk)

    assert s.error is None, f"nothing arrived while the backend was paused: {s.error!r}"
    assert seen_while_paused and seen_while_paused[0]
    assert s.raw == expected_stream(w.CHAT_BACKEND_MODEL, "live")


def test_stream_carries_rate_limit_headers_and_request_id(stack, world):
    s = stack.chat_stream(w.CHAT_ALIAS, headers={"X-Request-ID": "stream-rid-1"})

    assert s.headers["x-request-id"] == "stream-rid-1"
    assert s.headers["x-ratelimit-limit"] == "100000"
    assert int(s.headers["x-ratelimit-remaining"]) >= 0
    assert int(s.headers["x-ratelimit-reset"]) > 0


# ---- cancellation ----------------------------------------------------------


def test_streaming_client_disconnect_closes_the_backend_stream(stack, world):
    world.chat.stream_gap = 0.2
    body = {"model": w.CHAT_ALIAS, "messages": user("bye"), "stream": True}

    with stack.http.stream("POST", CHAT, json=body, headers=stack.key.auth) as response:
        next(response.iter_raw())  # first bytes are here; now hang up

    seen = world.chat.last(CHAT)
    wait_until(lambda: seen.aborted, timeout=5, what="the backend to see its stream aborted")
    assert not seen.completed


@pytest.mark.change("C16")
def test_non_streaming_client_disconnect_cancels_the_backend_call(stack, world):
    world.chat.delay(2.0)

    with pytest.raises(httpx.ReadTimeout):
        stack.chat(w.CHAT_ALIAS, timeout=0.3)  # the client gives up and closes the connection

    seen = world.chat.last(CHAT)
    wait_until(lambda: seen.aborted, timeout=1.2, what="the backend to see the call aborted")
    assert not seen.completed


@pytest.mark.legacy_only
def test_non_streaming_client_disconnect_backend_keeps_generating_today(stack, world):
    world.chat.delay(1.5)

    with pytest.raises(httpx.ReadTimeout):
        stack.chat(w.CHAT_ALIAS, timeout=0.3)

    seen = world.chat.last(CHAT)
    wait_until(lambda: seen.finished is not None, timeout=5, what="the backend call to end")
    assert seen.completed and not seen.aborted


# ---- C1: errors on streaming requests --------------------------------------

BACKEND_ERROR = {
    "error": {"message": "the backend said no", "type": "BadRequestError", "param": None, "code": 0}
}


@pytest.mark.change("C1")
@pytest.mark.parametrize("status", [400, 404, 422, 500, 503])
def test_stream_backend_error_status_reaches_the_client(stack, world, status):
    world.chat.fail(status, BACKEND_ERROR)

    s = stack.chat_stream(w.CHAT_ALIAS)

    assert s.status == status
    assert "the backend said no" in stream_error(s)["message"]


@pytest.mark.legacy_only
@pytest.mark.parametrize("status", [400, 500])
def test_stream_backend_error_status_is_an_empty_200_today(stack, world, status):
    world.chat.fail(status, BACKEND_ERROR)

    s = stack.chat_stream(w.CHAT_ALIAS)

    assert s.status == 200
    assert s.headers["content-type"].startswith("text/event-stream")
    assert s.raw == b""


@pytest.mark.change("C1")
def test_stream_connect_failure_is_a_json_503(factory):
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack([route("Solo", "chat", backend, models=["solo"], health_path=None)])
    backend.stop()

    s = stack.chat_stream("solo")

    assert s.status == 503
    error = stream_error(s)
    assert error.get("code") == "backend_unreachable"
    assert backend.url not in error["message"]


@pytest.mark.legacy_only
def test_stream_connect_failure_is_an_error_event_in_a_200_today(factory):
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack([route("Solo", "chat", backend, models=["solo"], health_path=None)])
    backend.stop()

    s = stack.chat_stream("solo")

    assert s.status == 200 and s.error is None
    assert len(s.data) == 2 and s.data[1] == "[DONE]"
    assert s.data[0]["error"]["message"].startswith("Backend unavailable: ")
    assert s.data[0]["error"]["type"] == "api_error"


@pytest.mark.change("C1")
@pytest.mark.parametrize("rst", [False, True], ids=["closed", "reset"])
def test_stream_dropped_midway_ends_with_an_error_event(stack, world, rst):
    world.chat.stream_gap = 0.01
    world.chat.drop(after=3, rst=rst)
    sent_before_drop = b"".join(
        FakeVLLM.render_stream({"model": w.CHAT_BACKEND_MODEL, "messages": user("hello")})[:3]
    )

    s = stack.chat_stream(w.CHAT_ALIAS)

    assert s.status == 200 and s.error is None
    assert s.raw.startswith(sent_before_drop)
    tail = sse_data(s.raw[len(sent_before_drop) :])
    assert len(tail) == 2, f"expected an error event and [DONE] after the break, got {tail!r}"
    assert isinstance(tail[0]["error"], dict) and tail[0]["error"]["message"]
    assert tail[1] == "[DONE]"
    assert s.raw.endswith(b"data: [DONE]\n\n")


@pytest.mark.legacy_only
def test_stream_dropped_midway_just_ends_today(stack, world):
    world.chat.stream_gap = 0.01
    world.chat.drop(after=3)
    sent_before_drop = b"".join(
        FakeVLLM.render_stream({"model": w.CHAT_BACKEND_MODEL, "messages": user("hello")})[:3]
    )

    s = stack.chat_stream(w.CHAT_ALIAS)

    assert s.status == 200
    assert s.raw == sent_before_drop  # no error event, no [DONE]


@pytest.mark.change("C1")
def test_stream_error_response_is_a_normal_router_error(stack, world):
    """A failed streaming request looks like any other failed request: envelope, request id."""
    world.chat.fail(400, BACKEND_ERROR)
    body = {"model": w.CHAT_ALIAS, "messages": user("x"), "stream": True}

    r = stack.post(CHAT, json=body, headers={"X-Request-ID": "c1-rid"})

    assert r.status_code == 400
    assert error_of(r)["type"] == "invalid_request_error"
    assert r.headers.get("x-request-id") == "c1-rid"
