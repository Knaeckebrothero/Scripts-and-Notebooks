"""Resilience: traffic cannot starve the control plane, clients cannot hold resources
forever, and a shutdown ends what is in flight properly. New router only.

Each test pins one finding of the review of 2026-11-19:

* probes and discovery have a connection pool of their own; a full traffic pool
  is ``503 router_busy`` and blames no backend;
* a streaming client that stops reading is dropped after ``ROUTER_CLIENT_WRITE_TIMEOUT``;
* ``ROUTER_DEFAULT_MAX_CONCURRENCY`` caps keys that have no cap of their own;
* SIGTERM: after ``ROUTER_SHUTDOWN_GRACE_SECONDS`` requests in flight are ended
  with an answer, recorded, and the process exits by itself;
* ``/v1/audio/voices`` (no key needed) asks a backend once, however many callers wait;
* metadata that changes on every ``/v1/models`` call is not a routing change;
* a connection that breaks after ``[DONE]`` is a clean end of the stream.
"""

from __future__ import annotations

import json
import signal
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.fakes.vllm import CREATED, model_entry
from tests.harness.stack import route
from tests.harness.util import HOST, error_of, holds_for, wait_until

CHAT = "/v1/chat/completions"
VOICES = "/v1/audio/voices"
DONE = b"data: [DONE]\n\n"
pytestmark = pytest.mark.feature("resilience")


def chat_route(name: str, backend, model: str) -> dict:
    return route(name, "chat", backend, backend_model_name=f"acme/{model}", models=[model])


def in_flight(fake, path: str = CHAT) -> int:
    return len([call for call in fake.calls(path) if call.finished is None])


def event(text: str) -> bytes:
    return b'data: {"choices":[{"index":0,"delta":{"content":' + json.dumps(text).encode() + b"}}]}\n\n"


def outcomes(stack, key) -> dict[str, int]:
    return {row["outcome"]: int(row["requests"]) for row in stack.db.usage_hourly(key)}


# ---- the traffic pool and the control plane --------------------------------


def test_a_full_traffic_pool_is_router_busy_and_takes_no_model_down(factory):
    """Two connections, two streams holding them: probes and discovery still work,
    and the request that finds no connection is told so after the pool timeout."""
    busy = factory.fake("vllm", ["acme/busy"], name="busy")
    good = factory.fake("vllm", ["acme/good"], name="good")
    stack = factory.stack(
        [chat_route("Busy", busy, "busy"), chat_route("Good", good, "good")],
        env={"ROUTER_UPSTREAM_MAX_CONNECTIONS": "2"},
        name="pool",
    )
    busy.pause_stream_after(1)

    with ThreadPoolExecutor(3) as pool:
        held = [pool.submit(stack.chat_stream, "busy") for _ in range(2)]
        wait_until(lambda: in_flight(busy) == 2, timeout=5, what="two streams to hold both connections")
        started = time.monotonic()
        waiting = pool.submit(stack.chat, "good")

        # Longer than it takes to mark a backend down (two failed probes, one per second).
        holds_for(
            lambda: (
                stack.get("/health", key=None).json()["routes"] == {"Busy": "up", "Good": "up"}
                and {"busy", "good", "acme/busy", "acme/good"} <= set(stack.models())
            ),
            seconds=3.5, interval=0.25, what="every route staying up while the traffic pool is full",
        )  # fmt: skip

        response = waiting.result(timeout=15)
        waited = time.monotonic() - started
        busy.resume_stream()
        assert [stream.result(timeout=10).status for stream in held] == [200, 200]

    assert response.status_code == 503, response.text
    assert error_of(response).get("code") == "router_busy"
    assert response.headers.get("retry-after") == "1"
    assert response.headers.get("x-router-error-source") == "router"
    assert 4.0 <= waited <= 9.0, f"the pool timeout is 5 s, the answer took {waited:.1f} s"
    assert not good.calls(CHAT), "the request never reached a backend"
    # Nobody was blamed: the route is up and takes the next request at once.
    assert stack.get("/health", key=None).json()["routes"]["Good"] == "up"
    assert stack.chat("good").status_code == 200
    wait_until(lambda: outcomes(stack, stack.key).get("unreachable") == 1, timeout=5, what="the usage row")


# ---- a client that stops reading ---------------------------------------------


def test_a_streaming_client_that_stops_reading_is_dropped(factory):
    backend = factory.fake("vllm", ["acme/big"])
    backend.stream_script = [event("x" * 65_000)] * 400 + [DONE]  # about 26 MB
    stack = factory.stack(
        [chat_route("Big", backend, "big")], env={"ROUTER_CLIENT_WRITE_TIMEOUT": "2"}, name="stall"
    )
    key = stack.new_key(max_concurrency=1)
    body = json.dumps(
        {"model": "big", "messages": [{"role": "user", "content": "x"}], "stream": True}
    ).encode()
    request = (
        f"POST {CHAT} HTTP/1.1\r\nHost: router\r\nAuthorization: Bearer {key.token}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode() + body

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    try:
        client.connect((HOST, stack.router.port))
        client.sendall(request)  # ... and never read the answer
        wait_until(
            lambda: backend.calls(CHAT) and backend.last(CHAT).sent, timeout=5, what="the stream to start"
        )
        stalled = backend.last(CHAT)
        assert stack.chat("big", key=key).status_code == 429, "the stalled stream holds the key's only slot"

        wait_until(lambda: stalled.aborted, timeout=10, what="the router to close the upstream connection")
        assert not stalled.completed
        wait_until(
            lambda: stack.chat("big", key=key).status_code == 200, timeout=5, what="the slot to be free"
        )
        wait_until(lambda: outcomes(stack, key).get("client_closed") == 1, timeout=5, what="the usage row")
    finally:
        client.close()


# ---- the default concurrency cap -----------------------------------------------


def test_default_concurrency_cap_applies_to_keys_without_one(factory):
    backend = factory.fake("vllm", ["acme/m"])
    stack = factory.stack(
        [chat_route("M", backend, "m")], env={"ROUTER_DEFAULT_MAX_CONCURRENCY": "1"}, name="defaultcap"
    )
    own_cap = stack.new_key(max_concurrency=2)
    backend.delay(1.0, path=CHAT)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(stack.chat, "m")  # the default key has no cap of its own
        wait_until(lambda: in_flight(backend) == 1, timeout=3, what="the first request in flight")
        second = stack.chat("m")
        other = pool.submit(stack.chat, "m", key=own_cap)
        wait_until(lambda: in_flight(backend) == 2, timeout=3, what="the other key's request in flight")
        also_other = stack.chat("m", key=own_cap)  # its own cap of 2 wins over the default of 1
        assert first.result(timeout=10).status_code == 200
        assert other.result(timeout=10).status_code == 200

    assert second.status_code == 429 and error_of(second).get("code") == "concurrency_limit_exceeded"
    assert second.headers.get("retry-after") == "1"
    assert also_other.status_code == 200


# ---- shutdown ---------------------------------------------------------------------


def test_sigterm_ends_requests_in_flight_with_an_answer_and_exits(factory):
    backend = factory.fake("vllm", ["acme/slow"])
    stack = factory.stack(
        [chat_route("Slow", backend, "slow")], env={"ROUTER_SHUTDOWN_GRACE_SECONDS": "1"}, name="shutdown"
    )
    backend.hang(path=CHAT, times=1)  # the plain request never gets an answer
    backend.pause_stream_after(2)  # the stream stops after two events

    with ThreadPoolExecutor(2) as pool:
        plain = pool.submit(stack.chat, "slow", headers={"X-Request-ID": "shutdown-plain"})
        wait_until(lambda: len(backend.calls(CHAT)) == 1, timeout=5, what="the plain request in flight")
        stream = pool.submit(stack.chat_stream, "slow", headers={"X-Request-ID": "shutdown-stream"})
        wait_until(
            lambda: len(backend.calls(CHAT)) == 2 and backend.calls(CHAT)[1].sent, timeout=5,
            what="the stream to start",
        )  # fmt: skip

        started = time.monotonic()
        stack.router.signal(signal.SIGTERM)
        code = stack.router.wait_exit(timeout=8)
        took = time.monotonic() - started
        if code is None:
            stack.router.stop(timeout=0.5)  # free the two waiting clients before failing
        assert code is not None, "the router is still running 8 s after SIGTERM (grace period: 1 s)"
        plain_response = plain.result(timeout=5)
        stream_result = stream.result(timeout=5)

    assert 0.8 <= took <= 5.0, f"exit took {took:.1f} s with a grace period of 1 s"

    assert plain_response.status_code == 503, plain_response.text
    assert error_of(plain_response).get("code") == "router_shutting_down"
    assert plain_response.headers.get("x-request-id") == "shutdown-plain"

    assert stream_result.status == 200 and stream_result.error is None, "the stream must end, not break"
    data = stream_result.data
    assert data[-1] == "[DONE]" and data[-2]["error"].get("code") == "router_shutting_down", data[-2:]
    assert len(data) >= 4, "the events sent before the shutdown are still there"

    # Both were recorded before the process went away, and the backend calls were abandoned.
    assert outcomes(stack, stack.key) == {"unavailable": 1, "midstream_error": 1}
    log = stack.router.log()
    assert "shutdown-plain" in log and "shutdown-stream" in log
    wait_until(
        lambda: all(call.aborted for call in backend.calls(CHAT)),
        timeout=3,
        what="the backend to see both aborts",
    )


# ---- voices: open to everyone, so it must not multiply load -------------------------


def tts_route(kokoro) -> dict:
    return route("Tts", "tts", kokoro, backend_model_name="kokoro", models=["tts"], health_path=None)


def test_concurrent_voices_callers_cause_one_backend_call(factory):
    kokoro = factory.fake("kokoro")
    stack = factory.stack([tts_route(kokoro)], name="voices")
    kokoro.delay(0.7, path=VOICES)

    with ThreadPoolExecutor(12) as pool:
        answers = list(pool.map(lambda _: stack.get(VOICES, key=None), range(12)))

    assert [a.status_code for a in answers] == [200] * 12
    assert all(a.json() == {"voices": kokoro.voices} for a in answers)
    assert len(kokoro.calls(VOICES)) == 1, "callers that arrive during a fetch wait for that fetch"


def test_a_failing_voices_backend_is_not_asked_for_every_caller(factory):
    kokoro = factory.fake("kokoro")
    stack = factory.stack([tts_route(kokoro)], name="voicesfail")
    kokoro.fail(503, {"detail": "starting up"}, path=VOICES)

    answers = [stack.get(VOICES, key=None) for _ in range(8)]

    assert [a.status_code for a in answers] == [503] * 8
    assert all(error_of(a)["message"] == "starting up" for a in answers)
    assert len(kokoro.calls(VOICES)) == 1, "the failure is remembered for a few seconds"
    # ... but only for a few seconds: once the backend is fine, so is the answer.
    kokoro.clear_faults()
    recovered = wait_until(
        lambda: (r := stack.get(VOICES, key=None)).status_code == 200 and r, timeout=10, interval=0.25,
        what="voices to recover after the failure expired",
    )  # fmt: skip
    assert recovered.json() == {"voices": kokoro.voices}
    assert len(kokoro.calls(VOICES)) == 2


# ---- discovery: metadata churn is not news ---------------------------------------------


def test_metadata_that_changes_on_every_listing_is_not_a_routing_change(factory):
    """vLLM reports a new ``created`` and a new permission id on every ``/v1/models`` call."""
    chat = factory.fake("vllm", ["acme/churn"])
    kokoro = factory.fake("kokoro")
    stack = factory.stack([chat_route("Churn", chat, "churn"), tts_route(kokoro)], name="churn")
    assert stack.get(VOICES, key=None).status_code == 200
    mark = stack.router.log_size()

    for step in (1, 2):  # discovery runs every 2 s in this suite
        chat.models = [
            model_entry("acme/churn", created=CREATED + step, permission=[{"id": f"modelperm-{step}"}])
        ]
        wait_until(
            lambda step=step: stack.models()["acme/churn"]["created"] == CREATED + step, timeout=8,
            what="the new metadata to show in /v1/models",
        )  # fmt: skip

    assert stack.models()["churn"]["permission"] == [{"id": "modelperm-2"}], "aliases follow too"
    assert stack.get(VOICES, key=None).status_code == 200
    assert len(kokoro.calls(VOICES)) == 1, "caches were not thrown away for a metadata change"
    assert "model table rebuilt" not in stack.router.log_since(mark), "and it is not logged as a change"
    assert stack.chat("churn").status_code == 200


# ---- streams: [DONE], then the connection breaks ------------------------------------------


def test_a_connection_that_breaks_after_done_is_a_clean_end(factory):
    backend = factory.fake("vllm", ["acme/m"])
    stack = factory.stack([chat_route("M", backend, "m")], name="done")
    first, second = event("Hel"), event("lo")
    backend.stream_script = [first, second, DONE, b": never sent\n\n"]
    backend.drop(after=3, rst=True, path=CHAT)  # everything up to [DONE], then a reset

    result = stack.chat_stream("m")

    assert backend.last(CHAT).dropped, "the fake did cut the connection"
    assert result.status == 200 and result.error is None
    assert result.raw == first + second + DONE, "no error event and no second [DONE]"
    wait_until(lambda: outcomes(stack, stack.key) == {"ok": 1}, timeout=5, what="the stream to count as ok")
