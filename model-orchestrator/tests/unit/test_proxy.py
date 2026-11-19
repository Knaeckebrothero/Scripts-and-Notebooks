"""Forwarding: failover rules, the error mapping table, streaming failures, cancellation (design 7.5).

No network: the upstream side is an ``httpx.MockTransport`` and the client side
is a pair of fake ASGI ``receive``/``send`` callables.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
from starlette.responses import Response

from model_orchestrator.clients import ClientPool
from model_orchestrator.context import RequestRecord
from model_orchestrator.errors import SOURCE_HEADER
from model_orchestrator.health import HealthMonitor
from model_orchestrator.metrics import Metrics
from model_orchestrator.proxy import Forwarder, ForwardPlan, UpstreamAborted, stream_error_event
from model_orchestrator.registry import Registry, Target

from .support import route_target, upstream_target

A, B = "http://backend-a.invalid:8000", "http://backend-b.invalid:8000"
Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


class Downstream:
    """The client's side of the ASGI connection."""

    def __init__(self) -> None:
        self.gone = asyncio.Event()
        self.messages: list[dict[str, Any]] = []
        # After this many body messages the client stops reading: writes block.
        self.stalls_after: int | None = None

    async def receive(self) -> dict[str, Any]:
        await self.gone.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: dict[str, Any]) -> None:
        bodies = sum(1 for m in self.messages if m["type"] == "http.response.body")
        if self.stalls_after is not None and message["type"] == "http.response.body":
            if bodies >= self.stalls_after:
                await asyncio.Event().wait()  # a full socket buffer: the write never completes
        self.messages.append(message)

    @property
    def status(self) -> int:
        return self.messages[0]["status"]

    @property
    def headers(self) -> dict[str, str]:
        return {k.decode().lower(): v.decode() for k, v in self.messages[0]["headers"]}

    @property
    def body(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.messages if m["type"] == "http.response.body")

    @property
    def complete(self) -> bool:
        last = self.messages[-1] if self.messages else {}
        return last.get("type") == "http.response.body" and not last.get("more_body", False)

    def json(self) -> Any:
        return json.loads(self.body)


class Rig:
    def __init__(
        self, handler: Handler, target: Target | None = None, *, model: str = "alias", **forwarder: Any
    ) -> None:
        self.calls: list[httpx.Request] = []

        async def recording(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return await handler(request)

        self.target = target or route_target(
            backends=(A, B), aliases=("alias",), backend_model_name="org/model"
        )
        registry = Registry([self.target])
        if self.target.is_upstream:
            registry.apply_listing(self.target, [{"id": model}])
        entry = registry.table.get(model)
        assert entry is not None
        self.entry = entry
        self.monitor = HealthMonitor(fails=3, unhealthy_seconds=30.0, gating=True)
        self.monitor.configure([self.target])
        self.metrics = Metrics(version="test", git_sha="test")
        self.clients = ClientPool(lambda key: httpx.MockTransport(recording))
        self.forwarder = Forwarder(self.clients, self.monitor, self.metrics, **forwarder)
        self.record = RequestRecord(
            request_id="rid-1", method="POST", path="/v1/chat/completions", route=entry.route_name
        )
        self.down = Downstream()

    def plan(self, **overrides: Any) -> ForwardPlan:
        base: dict[str, Any] = {
            "entry": self.entry,
            "method": "POST",
            "path": "/v1/chat/completions",
            "read_timeout_s": 600.0,
            "json_body": b'{"model":"org/model","messages":[]}',
        }
        base.update(overrides)
        return ForwardPlan(**base)

    async def run(self, plan: ForwardPlan | None = None) -> Downstream:
        """Forward and deliver the response, as the app and the middleware would."""
        try:
            response = await self.forwarder.forward(plan or self.plan(), self.record, self.down.receive)
            await response({"type": "http"}, self.down.receive, self.down.send)
        finally:
            await self.record.run_cleanups()
            await self.clients.aclose()
        return self.down

    @property
    def hosts(self) -> list[str]:
        return [call.url.host for call in self.calls]

    def marked(self, backend: str) -> bool:
        return self.monitor.passive_remaining(backend) > 0

    def in_flight(self) -> float:
        return self.metrics.in_flight.labels(self.entry.route_name)._value.get()


def json_response(status: int, payload: Any, **headers: str) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


CHAT_OK = {
    "id": "c1",
    "choices": [{"message": {"content": "hi", "reasoning": "hm"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
}


def by_host(**handlers: Handler) -> Handler:
    async def handler(request: httpx.Request) -> httpx.Response:
        return await handlers["a" if request.url.host.startswith("backend-a") else "b"](request)

    return handler


def answers(response_factory: Callable[[], httpx.Response]) -> Handler:
    async def handler(request: httpx.Request) -> httpx.Response:
        return response_factory()

    return handler


def raises(exc_type: type[Exception]) -> Handler:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("simulated", request=request)  # type: ignore[call-arg]

    return handler


OK = answers(lambda: json_response(200, CHAT_OK))


# -- success -----------------------------------------------------------------------------


async def test_success_is_relayed_with_status_body_and_content_type() -> None:
    rig = Rig(OK)
    down = await rig.run()
    assert down.status == 200 and down.complete
    assert down.json() == CHAT_OK, "unknown fields such as `reasoning` pass through"
    assert down.headers["content-type"] == "application/json"
    assert rig.record.outcome == "ok" and rig.record.attempts == 1 and rig.record.upstream_status == 200
    assert (rig.record.prompt_tokens, rig.record.completion_tokens) == (11, 7)
    assert rig.record.forwarded and rig.record.backend == A
    assert rig.in_flight() == 0


async def test_what_the_backend_receives() -> None:
    target = route_target(backends=(A,), aliases=("alias",), api_key="backend-secret")
    rig = Rig(OK, target)
    await rig.run()
    (request,) = rig.calls
    assert str(request.url) == f"{A}/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer backend-secret"
    assert request.headers["x-request-id"] == "rid-1"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["accept-encoding"] == "identity", "relayed bytes are never compressed"
    assert request.content == b'{"model":"org/model","messages":[]}'
    timeout = request.extensions["timeout"]
    assert timeout["connect"] == 10.0 and timeout["read"] == 600.0, "C17: 10 s connect everywhere"
    assert timeout["pool"] == 5.0, "waiting for a pooled connection has its own short timeout"


async def test_no_authorization_is_sent_without_a_backend_key() -> None:
    rig = Rig(OK)
    await rig.run()
    assert "authorization" not in rig.calls[0].headers


async def test_a_strange_request_id_does_not_break_forwarding() -> None:
    rig = Rig(OK)
    rig.record.request_id = "id-with-ümlaut"
    down = await rig.run()
    assert down.status == 200
    assert rig.calls[0].headers["x-request-id"] == "id-with-?mlaut"


async def test_non_json_bodies_are_relayed_untouched() -> None:
    audio = bytes(range(256)) * 4
    rig = Rig(answers(lambda: httpx.Response(200, content=audio, headers={"content-type": "audio/mpeg"})))
    down = await rig.run(rig.plan(count_tokens=False))
    assert down.body == audio and down.headers["content-type"] == "audio/mpeg"
    assert rig.record.prompt_tokens is None


async def test_multipart_parts_are_sent_in_order() -> None:
    rig = Rig(OK)
    parts = [
        ("file", ("a.wav", b"RIFFdata", "audio/wav")),
        ("model", (None, b"whisper-1")),
        ("timestamp_granularities[]", (None, b"word")),
        ("timestamp_granularities[]", (None, b"segment")),
    ]
    await rig.run(rig.plan(json_body=None, form_parts=parts, path="/v1/audio/transcriptions"))
    request = rig.calls[0]
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = request.content
    positions = [
        body.index(b'name="file"; filename="a.wav"'),
        body.index(b'name="model"'),
        body.index(b"word"),
        body.index(b"segment"),
    ]
    assert positions == sorted(positions)
    assert b"RIFFdata" in body and b"whisper-1" in body and b"Content-Type: audio/wav" in body


# -- the error mapping table ---------------------------------------------------------------


async def test_backend_4xx_keeps_status_and_message_and_is_not_retried() -> None:
    """C4: the envelope's message is the backend's own message."""
    rig = Rig(
        answers(
            lambda: json_response(
                400, {"error": {"message": "max_tokens is too large", "type": "BadRequestError", "code": 400}}
            )
        )
    )
    down = await rig.run()
    assert down.status == 400
    assert down.json() == {"error": {"message": "max_tokens is too large", "type": "invalid_request_error"}}
    assert down.headers[SOURCE_HEADER.lower()] == "backend"
    assert rig.record.outcome == "client_error" and rig.record.attempts == 1
    assert len(rig.calls) == 1, "a 4xx is the client's problem: no second attempt"
    assert not rig.marked(A), "and it says nothing about the backend's health"


async def test_backend_5xx_on_the_last_attempt_keeps_status_and_message() -> None:
    """C2: no more blanket 503."""
    rig = Rig(answers(lambda: json_response(500, {"error": {"message": "CUDA out of memory"}})))
    down = await rig.run()
    assert down.status == 500
    assert down.json() == {"error": {"message": "CUDA out of memory", "type": "api_error"}}
    assert down.headers[SOURCE_HEADER.lower()] == "backend"
    assert rig.record.outcome == "backend_error" and rig.record.upstream_status == 500
    assert rig.hosts == ["backend-a.invalid", "backend-b.invalid"], "two attempts on distinct backends"
    assert rig.marked(A) and rig.marked(B)


async def test_5xx_then_success_on_the_other_backend() -> None:
    rig = Rig(by_host(a=answers(lambda: json_response(503, {"detail": "loading"})), b=OK))
    down = await rig.run()
    assert down.status == 200 and down.json() == CHAT_OK
    assert rig.record.outcome == "ok" and rig.record.attempts == 2 and rig.record.backend == B
    assert rig.marked(A) and not rig.marked(B)


async def test_single_backend_gets_one_attempt() -> None:
    rig = Rig(
        answers(lambda: json_response(502, {"message": "bad gateway"})),
        route_target(backends=(A,), aliases=("alias",)),
    )
    down = await rig.run()
    assert down.status == 502 and down.json()["error"]["message"] == "bad gateway"
    assert len(rig.calls) == 1


async def test_at_most_two_attempts_even_in_a_larger_pool() -> None:
    target = route_target(backends=(A, B, "http://backend-c.invalid:8000"), aliases=("alias",))
    rig = Rig(raises(httpx.ConnectError), target)
    down = await rig.run()
    assert down.status == 503 and len(rig.calls) == 2


async def test_no_backend_reachable_is_503_backend_unreachable() -> None:
    rig = Rig(raises(httpx.ConnectError))
    down = await rig.run()
    assert down.status == 503
    assert down.json() == {
        "error": {
            "message": "No backend for this model could be reached",
            "type": "api_error",
            "code": "backend_unreachable",
        }
    }
    assert down.headers[SOURCE_HEADER.lower()] == "router"
    assert rig.record.outcome == "unreachable" and rig.record.attempts == 2
    assert rig.marked(A) and rig.marked(B)
    assert "invalid" not in down.body.decode(), "router messages never contain backend URLs"


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ReadError]
)
async def test_connection_failure_then_success_on_the_other_backend(error: type[Exception]) -> None:
    rig = Rig(by_host(a=raises(error), b=OK))
    down = await rig.run()
    assert down.status == 200 and rig.record.attempts == 2 and rig.record.outcome == "ok"


DISCONNECTS = [httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.CloseError]
DISCONNECTED = {
    "error": {
        "message": "The backend closed the connection before sending a complete response",
        "type": "api_error",
        "code": "backend_disconnected",
    }
}


@pytest.mark.parametrize("error", DISCONNECTS)
async def test_backend_that_breaks_the_connection_is_502_backend_disconnected(error: type[Exception]) -> None:
    rig = Rig(raises(error), route_target(backends=(A,), aliases=("alias",)))
    down = await rig.run()
    assert down.status == 502
    assert down.json() == DISCONNECTED
    assert down.headers[SOURCE_HEADER.lower()] == "router"
    assert rig.record.outcome == "backend_error" and rig.record.upstream_status is None
    assert rig.marked(A) and len(rig.calls) == 1


async def test_a_broken_connection_counts_as_a_failed_attempt_like_a_5xx() -> None:
    rig = Rig(raises(httpx.RemoteProtocolError))
    down = await rig.run()
    assert down.status == 502 and down.json() == DISCONNECTED
    assert rig.hosts == ["backend-a.invalid", "backend-b.invalid"], "the second backend was tried"
    assert rig.record.attempts == 2 and rig.marked(A) and rig.marked(B)


@pytest.mark.parametrize("stream", [False, True])
async def test_a_broken_connection_before_the_first_byte_of_a_stream_is_502_too(stream: bool) -> None:
    rig = Rig(raises(httpx.RemoteProtocolError), route_target(backends=(A,), aliases=("alias",)))
    down = await rig.run(rig.plan(stream=stream))
    assert down.status == 502 and down.json() == DISCONNECTED
    assert down.headers["content-type"] == "application/json"


async def test_broken_connection_and_unreachable_in_either_order_is_502() -> None:
    for a, b in (
        (httpx.RemoteProtocolError, httpx.ConnectError),
        (httpx.ConnectError, httpx.RemoteProtocolError),
    ):
        rig = Rig(by_host(a=raises(a), b=raises(b)))
        down = await rig.run()
        assert down.status == 502 and down.json() == DISCONNECTED
        assert rig.record.outcome == "backend_error"


async def test_the_latest_thing_a_backend_did_decides_between_5xx_and_502() -> None:
    answer_500 = answers(lambda: json_response(500, {"error": {"message": "boom"}}))
    first = Rig(by_host(a=answer_500, b=raises(httpx.ReadError)))
    down = await first.run()
    assert down.status == 502 and down.json() == DISCONNECTED
    second = Rig(by_host(a=raises(httpx.ReadError), b=answer_500))
    down = await second.run()
    assert down.status == 500 and down.json()["error"]["message"] == "boom"
    assert down.headers[SOURCE_HEADER.lower()] == "backend"
    assert first.record.outcome == second.record.outcome == "backend_error"


async def test_pool_timeout_is_503_router_busy_and_blames_no_backend() -> None:
    """Every connection in use is the router's limit, not a backend failure."""
    rig = Rig(raises(httpx.PoolTimeout))
    down = await rig.run()
    assert down.status == 503
    assert down.json() == {
        "error": {
            "message": "The router has no free connection for this request right now",
            "type": "api_error",
            "code": "router_busy",
        }
    }
    assert down.headers["retry-after"] == "1"
    assert down.headers[SOURCE_HEADER.lower()] == "router"
    assert rig.record.outcome == "unreachable" and rig.record.reason == "busy"
    assert len(rig.calls) == 1, "the other backend shares the pool: no second attempt"
    assert not rig.marked(A) and not rig.marked(B), "no backend is marked unhealthy"
    backend_failures = [s for m in rig.metrics.backend_failures.collect() for s in m.samples]
    assert backend_failures == [], "and none is counted as failing"
    assert rig.in_flight() == 0


@pytest.mark.parametrize("error", [httpx.ProxyError, httpx.UnsupportedProtocol])
async def test_never_reaching_a_backend_is_503_backend_unreachable(error: type[Exception]) -> None:
    rig = Rig(raises(error))
    down = await rig.run()
    assert down.status == 503 and down.json()["error"]["code"] == "backend_unreachable"
    assert rig.record.outcome == "unreachable" and rig.record.attempts == 2


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.WriteTimeout])
async def test_read_timeout_is_504_with_no_second_attempt(error: type[Exception]) -> None:
    """C3."""
    rig = Rig(by_host(a=raises(error), b=OK))
    down = await rig.run()
    assert down.status == 504
    assert down.json() == {
        "error": {
            "message": "The backend did not answer in time",
            "type": "api_error",
            "code": "backend_timeout",
        }
    }
    assert down.headers[SOURCE_HEADER.lower()] == "router"
    assert rig.record.outcome == "timeout"
    assert rig.hosts == ["backend-a.invalid"], "the other backend is not tried after a read timeout"
    assert rig.marked(A)


async def test_timeout_on_the_second_attempt_is_504() -> None:
    rig = Rig(by_host(a=answers(lambda: json_response(500, {})), b=raises(httpx.ReadTimeout)))
    down = await rig.run()
    assert down.status == 504 and rig.record.outcome == "timeout"


async def test_5xx_then_unreachable_returns_the_answer_a_backend_gave() -> None:
    rig = Rig(
        by_host(
            a=answers(lambda: json_response(500, {"error": {"message": "boom"}})),
            b=raises(httpx.ConnectError),
        )
    )
    down = await rig.run()
    assert down.status == 500 and down.json()["error"]["message"] == "boom"
    assert down.headers[SOURCE_HEADER.lower()] == "backend"
    assert rig.record.outcome == "backend_error" and rig.record.upstream_status == 500


async def test_unreachable_then_5xx_returns_the_5xx() -> None:
    rig = Rig(
        by_host(
            a=raises(httpx.ConnectError), b=answers(lambda: httpx.Response(503, text="Service Unavailable"))
        )
    )
    down = await rig.run()
    assert down.status == 503
    assert down.json() == {"error": {"message": "Service Unavailable", "type": "api_error"}}
    assert rig.record.outcome == "backend_error"


async def test_a_plain_backend_error_gets_no_code_and_no_retry_after() -> None:
    rig = Rig(
        answers(
            lambda: json_response(
                429, {"error": {"message": "slow down", "code": "rate_limited"}}, **{"Retry-After": "7"}
            )
        )
    )
    down = await rig.run()
    assert down.status == 429
    assert down.json() == {"error": {"message": "slow down", "type": "rate_limit_error"}}
    assert "retry-after" not in down.headers


async def test_an_upstream_routers_code_and_retry_after_are_kept() -> None:
    envelope = {
        "error": {
            "message": "Model 'm' is temporarily unavailable",
            "type": "api_error",
            "code": "model_unavailable",
        }
    }
    rig = Rig(
        answers(lambda: json_response(503, envelope, **{"Retry-After": "30"})), upstream_target(), model="m"
    )
    down = await rig.run()
    assert down.status == 503
    assert down.json() == envelope
    assert down.headers["retry-after"] == "30"
    assert down.headers[SOURCE_HEADER.lower()] == "backend"
    assert rig.calls[0].headers["authorization"] == "Bearer upstream-key"


# -- streaming -----------------------------------------------------------------------------


def sse(*events: bytes) -> Callable[[], httpx.Response]:
    def build() -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            for event in events:
                yield event

        return httpx.Response(
            200, headers={"content-type": "text/event-stream; charset=utf-8"}, content=body()
        )

    return build


def breaking_sse(error: Exception, *chunks: bytes) -> Callable[[], httpx.Response]:
    def build() -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk
            raise error

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    return build


E1 = b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n'
E2 = b'data: {"choices":[{"delta":{"reasoning":"hm"}}]}\n\n'
E3 = b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\n'
USAGE = b'data: {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":7,"total_tokens":18}}\n\n'
DONE = b"data: [DONE]\n\n"


async def test_stream_is_relayed_byte_for_byte() -> None:
    rig = Rig(answers(sse(E1, E2, E3, USAGE, DONE)))
    down = await rig.run(rig.plan(stream=True))
    assert down.status == 200 and down.complete
    assert down.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert "content-length" not in down.headers
    assert down.body == E1 + E2 + E3 + USAGE + DONE, "the client asked for usage: nothing is removed"
    assert rig.record.outcome == "ok" and rig.record.stream
    assert (rig.record.prompt_tokens, rig.record.completion_tokens) == (11, 7)
    assert rig.in_flight() == 0


async def test_the_usage_only_event_is_not_relayed_when_the_router_asked_for_it() -> None:
    # Awkward chunking on purpose: boundaries inside events and inside terminators.
    stream = E1 + E2 + E3 + USAGE + DONE
    pieces = [stream[:10], stream[10 : len(E1) + 1], stream[len(E1) + 1 : -20], stream[-20:-1], stream[-1:]]
    rig = Rig(answers(sse(*pieces)))
    down = await rig.run(rig.plan(stream=True, strip_usage_event=True))
    assert down.body == E1 + E2 + E3 + DONE
    assert (rig.record.prompt_tokens, rig.record.completion_tokens) == (11, 7)
    assert rig.record.outcome == "ok"


async def test_only_whole_events_reach_the_client() -> None:
    rig = Rig(answers(sse(E1[:20], E1[20:] + E2[:5], E2[5:], DONE)))
    down = await rig.run(rig.plan(stream=True))
    bodies = [m["body"] for m in down.messages if m["type"] == "http.response.body" and m["body"]]
    assert bodies == [E1, E2, DONE]


async def test_stream_error_status_before_the_first_byte_is_a_real_status() -> None:
    """C1: no more HTTP 200 with an empty or error body."""
    target = route_target(backends=(A,), aliases=("alias",))
    rig = Rig(answers(lambda: json_response(400, {"error": {"message": "prompt too long"}})), target)
    down = await rig.run(rig.plan(stream=True))
    assert down.status == 400
    assert down.headers["content-type"] == "application/json"
    assert down.json() == {"error": {"message": "prompt too long", "type": "invalid_request_error"}}
    assert rig.record.outcome == "client_error"


async def test_streaming_requests_fail_over_before_the_first_byte() -> None:
    rig = Rig(by_host(a=answers(lambda: json_response(500, {"error": "loading"})), b=answers(sse(E1, DONE))))
    down = await rig.run(rig.plan(stream=True))
    assert down.status == 200 and down.body == E1 + DONE
    assert rig.record.attempts == 2 and rig.record.outcome == "ok"


async def test_streaming_connect_failure_on_every_backend_is_503_not_200() -> None:
    rig = Rig(raises(httpx.ConnectError))
    down = await rig.run(rig.plan(stream=True))
    assert down.status == 503 and down.json()["error"]["code"] == "backend_unreachable"


async def test_streaming_timeout_before_headers_is_504() -> None:
    rig = Rig(raises(httpx.ReadTimeout))
    down = await rig.run(rig.plan(stream=True))
    assert down.status == 504 and down.json()["error"]["code"] == "backend_timeout"
    assert len(rig.calls) == 1


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            httpx.RemoteProtocolError("peer closed connection"),
            "The backend connection was lost before the response was complete",
        ),
        (httpx.ReadError("reset"), "The backend connection was lost before the response was complete"),
        (httpx.ReadTimeout("silent"), "The backend stopped sending data before the response was complete"),
    ],
)
async def test_a_stream_that_breaks_gets_an_error_event_then_done(error: Exception, message: str) -> None:
    rig = Rig(answers(breaking_sse(error, E1, E2)), route_target(backends=(A,), aliases=("alias",)))
    down = await rig.run(rig.plan(stream=True))
    assert down.status == 200 and down.complete, "the status was already sent"
    assert down.body == E1 + E2 + stream_error_event(message)
    tail = down.body[len(E1 + E2) :].decode()
    assert tail.endswith("data: [DONE]\n\n")
    assert json.loads(tail.split("\n\n")[0].removeprefix("data: ")) == {
        "error": {"message": message, "type": "api_error"}
    }
    assert rig.record.outcome == "midstream_error", "a broken stream is not a success"
    assert rig.marked(A)
    assert len(rig.calls) == 1, "no retry once bytes have been sent"


async def test_a_half_received_event_is_dropped_before_the_error_event() -> None:
    rig = Rig(answers(breaking_sse(httpx.ReadError("reset"), E1, E2[:17])))
    down = await rig.run(rig.plan(stream=True))
    assert down.body == E1 + stream_error_event(
        "The backend connection was lost before the response was complete"
    )


async def test_tokens_seen_before_a_break_are_still_recorded() -> None:
    rig = Rig(answers(breaking_sse(httpx.ReadError("reset"), E1, USAGE)))
    await rig.run(rig.plan(stream=True, strip_usage_event=True))
    assert rig.record.outcome == "midstream_error"
    assert (rig.record.prompt_tokens, rig.record.completion_tokens) == (11, 7)


async def test_an_error_event_from_the_backend_is_relayed_and_counted_as_a_broken_stream() -> None:
    backend_error = b'data: {"error":{"message":"engine died","type":"api_error"}}\n\n'
    rig = Rig(answers(sse(E1, backend_error, DONE)))
    down = await rig.run(rig.plan(stream=True))
    assert down.body == E1 + backend_error + DONE
    assert rig.record.outcome == "midstream_error"


@pytest.mark.parametrize(
    "error", [httpx.RemoteProtocolError("reset"), httpx.ReadError("reset"), httpx.ReadTimeout("idle")]
)
async def test_a_connection_that_breaks_after_done_is_a_clean_end(error: Exception) -> None:
    """The client has the whole stream: no error event, no second [DONE], and it counts as ok."""
    rig = Rig(
        answers(breaking_sse(error, E1, E3, USAGE, DONE)), route_target(backends=(A,), aliases=("alias",))
    )
    down = await rig.run(rig.plan(stream=True, strip_usage_event=True))
    assert down.body == E1 + E3 + DONE
    assert down.complete
    assert rig.record.outcome == "ok" and rig.record.error is None
    assert (rig.record.prompt_tokens, rig.record.completion_tokens) == (11, 7)
    assert not rig.marked(A), "the backend delivered everything"


async def test_done_in_the_middle_of_a_chunk_counts_too() -> None:
    stream = E1 + DONE
    rig = Rig(answers(breaking_sse(httpx.ReadError("reset"), stream[:-3], stream[-3:])))
    down = await rig.run(rig.plan(stream=True))
    assert down.body == stream and rig.record.outcome == "ok"


async def test_a_break_before_done_is_still_an_error() -> None:
    not_done = b'data: {"choices":[{"delta":{"content":"the marker is [DONE]"}}]}\n\n'
    rig = Rig(answers(breaking_sse(httpx.ReadError("reset"), E1, not_done)))
    down = await rig.run(rig.plan(stream=True))
    assert down.body.startswith(E1 + not_done + b'data: {"error"')
    assert rig.record.outcome == "midstream_error"


async def test_unterminated_tail_of_a_clean_stream_is_relayed() -> None:
    rig = Rig(answers(sse(E1, b"data: [DONE]")))
    down = await rig.run(rig.plan(stream=True))
    assert down.body == E1 + b"data: [DONE]"


async def test_a_streamed_body_that_is_not_sse_is_relayed_raw() -> None:
    def build() -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            yield b'{"id": "c1", '
            yield b'"choices": []}'

        return httpx.Response(200, headers={"content-type": "application/json"}, content=body())

    rig = Rig(answers(build))
    down = await rig.run(rig.plan(stream=True))
    assert down.body == b'{"id": "c1", "choices": []}' and down.headers["content-type"] == "application/json"


async def test_a_broken_non_sse_stream_aborts_the_connection() -> None:
    def build() -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            yield b'{"id": '
            raise httpx.ReadError("reset")

        return httpx.Response(200, headers={"content-type": "application/json"}, content=body())

    rig = Rig(answers(build))
    with pytest.raises(UpstreamAborted):
        await rig.run(rig.plan(stream=True))
    assert not rig.down.complete, "no clean end: the client must see the response as incomplete"
    assert rig.record.outcome == "midstream_error"


# -- the client goes away -------------------------------------------------------------------


class Hanging:
    """A backend that never answers, and notices when the call is cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


@pytest.mark.parametrize("stream", [False, True])
async def test_client_disconnect_cancels_the_upstream_call(stream: bool) -> None:
    """C16, and the same for a stream that has not started."""
    backend = Hanging()
    rig = Rig(backend)
    forwarding = asyncio.create_task(
        rig.forwarder.forward(rig.plan(stream=stream), rig.record, rig.down.receive)
    )
    await asyncio.wait_for(backend.started.wait(), 2)
    assert not forwarding.done()
    rig.down.gone.set()
    response = await asyncio.wait_for(forwarding, 2)
    assert backend.cancelled, "the backend call must not keep running"
    assert isinstance(response, Response) and response.status_code == 499
    assert rig.record.outcome == "client_closed"
    assert len(rig.calls) == 1, "no failover for a client that left"
    await rig.record.run_cleanups()
    assert rig.in_flight() == 0
    await rig.clients.aclose()


async def test_client_disconnect_mid_stream_closes_the_upstream() -> None:
    closed = asyncio.Event()
    second_chunk = asyncio.Event()

    def build() -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            try:
                yield E1
                second_chunk.set()
                await asyncio.sleep(3600)
                yield E2
            finally:
                closed.set()

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    rig = Rig(answers(build))
    response = await rig.forwarder.forward(rig.plan(stream=True), rig.record, rig.down.receive)
    delivering = asyncio.create_task(response({"type": "http"}, rig.down.receive, rig.down.send))
    await asyncio.wait_for(second_chunk.wait(), 2)
    await asyncio.sleep(0.01)
    assert rig.down.body == E1 and not delivering.done()
    rig.down.gone.set()
    await asyncio.wait_for(delivering, 2)
    await asyncio.wait_for(closed.wait(), 2)
    assert rig.record.outcome == "client_closed"
    assert not rig.down.complete
    await rig.record.run_cleanups()
    assert rig.in_flight() == 0
    await rig.clients.aclose()


# -- a client that stops reading ----------------------------------------------------------------


def endless_sse(sent: list[httpx.Response]) -> Callable[[], httpx.Response]:
    """A stream that never ends. ``sent`` receives the response, to see whether it gets closed."""

    def build() -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            while True:
                yield E2
                await asyncio.sleep(0)

        response = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())
        sent.append(response)
        return response

    return build


async def test_a_client_that_stops_reading_is_dropped_after_the_write_timeout() -> None:
    """Connected but not reading: without this the slot and the upstream connection are held forever."""
    upstream: list[httpx.Response] = []
    rig = Rig(answers(endless_sse(upstream)), client_write_timeout_s=0.1)
    rig.down.stalls_after = 3
    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(rig.run(rig.plan(stream=True)), 3)
    assert 0.08 <= asyncio.get_running_loop().time() - started < 1.5
    assert upstream[0].is_closed, "the upstream connection is closed"
    assert rig.record.outcome == "client_closed"
    assert "took no data" in (rig.record.error or "")
    assert rig.down.body == E2 * 3 and not rig.down.complete, "nothing more is written to it"
    assert rig.in_flight() == 0
    assert not rig.marked(A), "not the backend's fault"


async def test_write_timeout_zero_means_wait_forever() -> None:
    upstream: list[httpx.Response] = []
    rig = Rig(answers(endless_sse(upstream)), client_write_timeout_s=0.0)
    rig.down.stalls_after = 1
    running = asyncio.create_task(rig.run(rig.plan(stream=True)))
    await asyncio.sleep(0.3)
    assert not running.done() and not upstream[0].is_closed
    rig.down.gone.set()
    await asyncio.wait_for(running, 2)
    assert rig.record.outcome == "client_closed" and upstream[0].is_closed


async def test_a_slow_but_reading_client_is_not_dropped() -> None:
    class Slow(Downstream):
        async def send(self, message: dict[str, Any]) -> None:
            await asyncio.sleep(0.03)
            await super().send(message)

    rig = Rig(answers(sse(E1, E2, E3, DONE)), client_write_timeout_s=0.2)
    rig.down = Slow()
    down = await rig.run(rig.plan(stream=True))
    assert down.body == E1 + E2 + E3 + DONE and down.complete and rig.record.outcome == "ok"


# -- the server shuts down -----------------------------------------------------------------------


async def shutting_down() -> bool:
    return True


async def cancel_mid_stream(rig: Rig) -> tuple[asyncio.Task[None], asyncio.Event]:
    """Start a stream that pauses after its first event and cancel its delivery, as the server does."""
    closed = asyncio.Event()
    first_sent = asyncio.Event()

    def build() -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            try:
                yield E1 + E2[:9]
                first_sent.set()
                await asyncio.sleep(3600)
            finally:
                closed.set()

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    rig.calls.clear()
    rig.forwarder._clients = ClientPool(lambda key: httpx.MockTransport(answers(build)))
    response = await rig.forwarder.forward(rig.plan(stream=True), rig.record, rig.down.receive)
    delivering: asyncio.Task[None] = asyncio.create_task(
        response({"type": "http"}, rig.down.receive, rig.down.send)
    )
    await asyncio.wait_for(first_sent.wait(), 2)
    await asyncio.sleep(0.01)
    delivering.cancel()
    return delivering, closed


async def test_a_stream_the_server_ends_at_shutdown_gets_an_error_event_and_done() -> None:
    rig = Rig(OK, shutting_down=shutting_down)
    delivering, closed = await cancel_mid_stream(rig)
    await asyncio.wait_for(delivering, 2)  # ends normally: the cancellation was turned into an ending
    await rig.record.run_cleanups()
    assert closed.is_set(), "the upstream is closed"
    tail = rig.down.body[len(E1) :]
    assert rig.down.body.startswith(E1) and E2[:9] not in tail, "the half event is not sent"
    assert tail == stream_error_event("The router is shutting down", "router_shutting_down")
    assert json.loads(tail.split(b"\n\n")[0].removeprefix(b"data: ")) == {
        "error": {
            "message": "The router is shutting down",
            "type": "api_error",
            "code": "router_shutting_down",
        }
    }
    assert tail.endswith(b"data: [DONE]\n\n") and rig.down.complete
    assert rig.record.outcome == "midstream_error"
    assert rig.in_flight() == 0


async def test_a_cancelled_stream_outside_shutdown_stays_cancelled() -> None:
    rig = Rig(OK)
    delivering, closed = await cancel_mid_stream(rig)
    with pytest.raises(asyncio.CancelledError):
        await delivering
    await rig.record.run_cleanups()
    assert closed.is_set()
    assert rig.down.body == E1 and not rig.down.complete
    assert rig.record.outcome == "client_closed"


async def test_cleanups_close_an_upstream_stream_that_was_never_delivered() -> None:
    closed = asyncio.Event()

    def build() -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            try:
                yield E1
            finally:
                closed.set()

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    rig = Rig(answers(build))
    response = await rig.forwarder.forward(rig.plan(stream=True), rig.record, rig.down.receive)
    assert response.status_code == 200
    await rig.record.run_cleanups()  # the response object is dropped without being sent
    assert rig.in_flight() == 0
    await rig.clients.aclose()
