"""Forwarding: one request to one target, with failover, error mapping and cancellation.

Rules (design 7.5):

* Up to two attempts on distinct backends of a pool. A second attempt follows a
  failure to reach the backend, a connection the backend broke off before a
  complete response, or a 5xx answer. It never follows a read timeout or a 4xx.
* The upstream response headers are awaited before the client gets an answer,
  for streaming requests too. So a failure before the first byte is a real HTTP
  status with a JSON error; only a failure after the stream has started is
  reported inside the stream, as an error event followed by ``[DONE]``.
* If the client goes away, the upstream call is cancelled, streaming or not.
  The same happens to a stream whose client stays connected but stops reading.
* A request that cannot get a connection from the pool is answered
  ``503 router_busy``. That is the router's own limit: no backend is blamed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx
from starlette.responses import Response
from starlette.types import Message, Receive, Scope, Send

from . import usage
from .clients import ClientPool, request_timeout
from .context import RequestRecord
from .errors import (
    CODE_BACKEND_DISCONNECTED,
    CODE_BACKEND_TIMEOUT,
    CODE_BACKEND_UNREACHABLE,
    CODE_ROUTER_BUSY,
    CODE_SHUTTING_DOWN,
    error_payload,
    error_response,
    extract_backend_code,
    extract_backend_message,
)
from .health import HealthMonitor, HealthProber
from .metrics import Metrics
from .registry import ModelEntry, Target
from .sse import SSESplitter, StreamInspector, TokenUsage, usage_from_body

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
# An error status has been received; its body is only wanted for the message.
ERROR_BODY_TIMEOUT_S = 10.0
CLIENT_CLOSED_STATUS = 499  # nginx's convention; the client is gone, this is for the log line

_READ_TIMEOUTS = (httpx.ReadTimeout, httpx.WriteTimeout)
# The request never got as far as a backend that could answer it.
_UNREACHABLE = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ProxyError,
    httpx.UnsupportedProtocol,
    httpx.InvalidURL,
)
_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)
_STREAM_ERRORS = (httpx.HTTPError, httpx.StreamError)

MSG_UNREACHABLE = "No backend for this model could be reached"
MSG_DISCONNECTED = "The backend closed the connection before sending a complete response"
MSG_TIMEOUT = "The backend did not answer in time"
MSG_STREAM_BROKEN = "The backend connection was lost before the response was complete"
MSG_STREAM_TIMEOUT = "The backend stopped sending data before the response was complete"
MSG_BUSY = "The router has no free connection for this request right now"
MSG_SHUTTING_DOWN = "The router is shutting down"
RETRY_AFTER_BUSY = "1"
# While the server is ending requests, a last write to a client may take this long.
SHUTDOWN_WRITE_TIMEOUT_S = 2.0

# One multipart part as httpx takes it: (field name, (filename or None, content[, content type])).
FormPart = tuple[str, tuple[Any, ...]]


class ClientDisconnected(Exception):
    """The client went away while the router was waiting for a backend."""


class UpstreamAborted(Exception):
    """A streamed non-SSE body broke off. The only honest signal left is to drop the connection."""


class ClientStalled(Exception):
    """The client of a stream stayed connected but took no bytes for too long."""


# Asked by a request that was cancelled: is the server ending requests (shutdown)?
ShuttingDown = Callable[[], Awaitable[bool]]


async def _not_shutting_down() -> bool:
    return False


def absorb_cancellation() -> None:
    """The current task was cancelled and carries on anyway, to end its request properly."""
    task = asyncio.current_task()
    if task is not None:
        task.uncancel()


@dataclass
class ForwardPlan:
    entry: ModelEntry
    method: str
    # Path on the backend: the route's endpoint, or the request path for an upstream router.
    path: str
    read_timeout_s: float
    json_body: bytes | None = None
    form_parts: list[FormPart] | None = None
    params: Mapping[str, str] | None = None
    stream: bool = False
    # The router asked for the usage event itself, so it is not relayed.
    strip_usage_event: bool = False
    # Read token counts from the response (not for speech endpoints).
    count_tokens: bool = True


class DisconnectWatcher:
    """Waits for the client's ``http.disconnect`` once the request body has been read."""

    def __init__(self, receive: Receive) -> None:
        self.task: asyncio.Task[None] = asyncio.ensure_future(self._watch(receive))

    @staticmethod
    async def _watch(receive: Receive) -> None:
        try:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # A server that cannot be asked again: stop watching, never report a disconnect.
            logger.debug("disconnect watcher: receive() failed", exc_info=True)
            await asyncio.Event().wait()

    @property
    def disconnected(self) -> bool:
        return self.task.done() and not self.task.cancelled()

    async def guard[T](
        self, awaitable: Awaitable[T], *, discard: Callable[[T], Awaitable[None]] | None = None
    ) -> T:
        """Run ``awaitable``; cancel it and raise ``ClientDisconnected`` if the client leaves first.

        ``discard`` releases a result that arrived while the cancellation was under way.
        """
        work: asyncio.Future[T] = asyncio.ensure_future(awaitable)
        racing: set[asyncio.Future[Any]] = {work, self.task}
        try:
            await asyncio.wait(racing, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            await _cancel(work)
            await _discard(work, discard)
            raise
        if work.done():
            return work.result()
        await _cancel(work)
        await _discard(work, discard)
        raise ClientDisconnected()

    async def close(self) -> None:
        await _cancel(self.task)


async def _cancel(task: asyncio.Future[Any]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _discard[T](work: asyncio.Future[T], discard: Callable[[T], Awaitable[None]] | None) -> None:
    if discard is None or work.cancelled() or work.exception() is not None:
        return
    with contextlib.suppress(Exception):
        await discard(work.result())


def stream_error_event(message: str, code: str | None = None) -> bytes:
    """What a client sees when a stream breaks after it has started."""
    payload = json.dumps(error_payload(502, message, code=code), ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\ndata: [DONE]\n\n".encode()


class Forwarder:
    def __init__(
        self,
        clients: ClientPool,
        monitor: HealthMonitor,
        metrics: Metrics,
        prober: HealthProber | None = None,
        *,
        client_write_timeout_s: float = 0.0,
        shutting_down: ShuttingDown = _not_shutting_down,
    ) -> None:
        self._clients = clients
        self._monitor = monitor
        self._metrics = metrics
        self._prober = prober
        self._client_write_timeout_s = client_write_timeout_s
        self._shutting_down = shutting_down

    async def forward(self, plan: ForwardPlan, record: RequestRecord, receive: Receive) -> Response:
        """Send the request and build the client's response. Never raises for a backend failure."""
        target = plan.entry.target
        route = record.route or plan.entry.route_name
        client = self._clients.get(target.client_key)
        record.forwarded = True
        record.stream = plan.stream
        gauge = self._metrics.in_flight.labels(route)
        gauge.inc()
        record.add_cleanup(gauge.dec)
        watcher = DisconnectWatcher(receive)
        record.add_cleanup(watcher.close)

        tried: set[str] = set()
        # What the client gets when no attempt succeeds: the latest thing a backend
        # did (a 5xx answer, or breaking the connection). An attempt that reached
        # no backend at all never replaces that.
        last_5xx: tuple[int, bytes, str | None] | None = None
        disconnected = False
        for _ in range(min(MAX_ATTEMPTS, len(target.backends))):
            backend = self._monitor.select(target, exclude=tried)
            if backend is None:
                break
            tried.add(backend)
            record.attempts += 1
            record.backend = backend
            try:
                request = self._build_request(client, plan, backend, record.request_id)
                response = await watcher.guard(
                    client.send(request, stream=plan.stream), discard=httpx.Response.aclose
                )
            except ClientDisconnected:
                return self._client_closed(record)
            except httpx.PoolTimeout:
                # Every connection of this network path is in use. Another backend
                # shares the same pool, so there is no second attempt, and no backend
                # is marked: none of them did anything wrong.
                logger.warning("no free upstream connection within the pool timeout")
                record.outcome = usage.UNREACHABLE
                record.reason = "busy"
                return error_response(
                    503, MSG_BUSY, code=CODE_ROUTER_BUSY, headers={"Retry-After": RETRY_AFTER_BUSY}
                )
            except _READ_TIMEOUTS:
                self._failed(target, route, backend, "timeout")
                record.outcome = usage.TIMEOUT
                return error_response(504, MSG_TIMEOUT, code=CODE_BACKEND_TIMEOUT)
            except _UNREACHABLE as exc:
                logger.info("attempt %d could not reach the backend: %s", record.attempts, type(exc).__name__)
                self._failed(target, route, backend, "connect")
                if self._prober is not None and isinstance(exc, _CONNECT_ERRORS):
                    self._prober.request_probe(target, backend)
                continue
            except httpx.HTTPError as exc:
                # The backend was there and broke the connection off (RemoteProtocolError,
                # ReadError and relatives). Nothing has been sent to the client yet.
                logger.info("attempt %d: backend disconnected: %s", record.attempts, type(exc).__name__)
                self._failed(target, route, backend, "disconnect")
                disconnected = True
                continue
            if plan.stream:
                record.add_cleanup(response.aclose)
            status = response.status_code
            record.upstream_status = status
            if status >= 400:
                try:
                    body = await watcher.guard(self._error_body(response, plan.stream))
                except ClientDisconnected:
                    return self._client_closed(record)
                retry_after = response.headers.get("retry-after")
                if status >= 500:
                    self._failed(target, route, backend, "http_5xx")
                    last_5xx = (status, body, retry_after)
                    disconnected = False
                    continue
                record.outcome = usage.CLIENT_ERROR
                return self._backend_error(target, status, body, retry_after)
            if plan.stream:
                return StreamRelay(
                    response,
                    record=record,
                    watcher=watcher,
                    strip_usage_event=plan.strip_usage_event,
                    count_tokens=plan.count_tokens,
                    on_break=partial(self._failed, target, route, backend),
                    write_timeout_s=self._client_write_timeout_s,
                    shutting_down=self._shutting_down,
                )
            return self._buffered(plan, record, response)

        if disconnected:
            record.upstream_status = None
            record.outcome = usage.BACKEND_ERROR
            return error_response(502, MSG_DISCONNECTED, code=CODE_BACKEND_DISCONNECTED)
        if last_5xx is not None:
            # A backend did answer, with an error of its own: that is what the client gets.
            status, body, retry_after = last_5xx
            record.upstream_status = status
            record.outcome = usage.BACKEND_ERROR
            return self._backend_error(target, status, body, retry_after)
        record.outcome = usage.UNREACHABLE
        return error_response(503, MSG_UNREACHABLE, code=CODE_BACKEND_UNREACHABLE)

    # -- pieces -----------------------------------------------------------------

    def _build_request(
        self, client: httpx.AsyncClient, plan: ForwardPlan, backend: str, request_id: str
    ) -> httpx.Request:
        target = plan.entry.target
        # The client's own Authorization header is never forwarded. The request id
        # is; a client may have put anything in it, so it is made header-safe.
        # Accept-Encoding: identity, so the bytes of a relayed stream are never compressed.
        headers = {
            "X-Request-ID": request_id.encode("ascii", errors="replace").decode("ascii"),
            "Accept-Encoding": "identity",
        }
        if target.api_key:
            headers["Authorization"] = f"Bearer {target.api_key}"
        url = f"{backend}{plan.path}"
        timeout = request_timeout(plan.read_timeout_s)
        params = dict(plan.params) if plan.params else None
        if plan.json_body is not None:
            headers["Content-Type"] = "application/json"
            return client.build_request(
                plan.method, url, content=plan.json_body, params=params, headers=headers, timeout=timeout
            )
        if plan.form_parts is not None:
            return client.build_request(
                plan.method, url, files=plan.form_parts, params=params, headers=headers, timeout=timeout
            )
        return client.build_request(plan.method, url, params=params, headers=headers, timeout=timeout)

    @staticmethod
    def _backend_error(target: Target, status: int, body: bytes, retry_after: str | None) -> Response:
        """The backend's own status and message, in the router's envelope.

        An upstream router speaks the same error dialect, so its ``code`` and
        ``Retry-After`` are kept: a client gets the same answer through two
        routers as through one.
        """
        code = None
        headers = {}
        if target.is_upstream:
            code = extract_backend_code(body)
            if retry_after:
                headers["Retry-After"] = retry_after
        return error_response(
            status, extract_backend_message(body, status), code=code, headers=headers, source="backend"
        )

    @staticmethod
    async def _error_body(response: httpx.Response, streamed: bool) -> bytes:
        if not streamed:
            return response.content
        try:
            async with asyncio.timeout(ERROR_BODY_TIMEOUT_S):
                return await response.aread()
        except (*_STREAM_ERRORS, TimeoutError):
            return b""
        finally:
            await response.aclose()

    def _buffered(self, plan: ForwardPlan, record: RequestRecord, response: httpx.Response) -> Response:
        body = response.content
        content_type = response.headers.get("content-type", "application/json")
        if plan.count_tokens and "json" in content_type.lower():
            _set_tokens(record, usage_from_body(body))
        record.outcome = usage.OK
        return Response(
            content=body, status_code=response.status_code, headers={"content-type": content_type}
        )

    def _failed(self, target: Target, route: str, backend: str, kind: str) -> None:
        self._monitor.mark_passive(backend)
        self._metrics.backend_failures.labels(route, backend, kind).inc()

    @staticmethod
    def _client_closed(record: RequestRecord) -> Response:
        record.outcome = usage.CLIENT_CLOSED
        return Response(status_code=CLIENT_CLOSED_STATUS)


def _set_tokens(record: RequestRecord, found: TokenUsage | None) -> None:
    if found is not None:
        record.prompt_tokens = found.prompt_tokens
        record.completion_tokens = found.completion_tokens


class StreamRelay(Response):
    """Relays an upstream stream to the client and watches for the client leaving.

    For an event stream the bytes go through the SSE splitter, so that only whole
    events reach the client. That is what makes it possible to leave out the
    usage-only event and to append a well-formed error event when the upstream
    breaks. Any other body is relayed as it comes.

    The relay ends in one of these ways: the upstream ends (or breaks), the client
    disconnects, the client stops reading for ``write_timeout_s``, or the server
    shuts down. In every case the upstream response is closed.
    """

    def __init__(
        self,
        upstream: httpx.Response,
        *,
        record: RequestRecord,
        watcher: DisconnectWatcher,
        strip_usage_event: bool,
        count_tokens: bool,
        on_break: Callable[[str], None],
        write_timeout_s: float = 0.0,
        shutting_down: ShuttingDown = _not_shutting_down,
    ) -> None:
        content_type = upstream.headers.get("content-type", "text/event-stream")
        self.status_code = upstream.status_code
        self.media_type = None
        self.background = None
        self.init_headers({"content-type": content_type})
        self._upstream = upstream
        self._record = record
        self._watcher = watcher
        self._on_break = on_break
        self._write_timeout_s = write_timeout_s
        self._shutting_down = shutting_down
        self._is_sse = content_type.lower().startswith("text/event-stream")
        self._count_tokens = count_tokens
        self._inspector = StreamInspector(strip_usage_only=strip_usage_event)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
        pump: asyncio.Task[None] = asyncio.ensure_future(self._pump(send))
        try:
            await asyncio.wait({pump, self._watcher.task}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            complete = pump.done() and not pump.cancelled() and pump.exception() is None
            await _cancel(pump)
            if not await self._shutting_down():
                raise
            # The server is shutting down and its grace period is over: tell the
            # client so, instead of leaving it with a stream that just stops.
            absorb_cancellation()
            await self._end_for_shutdown(send, complete)
            return
        if not pump.done():
            # The client went away: stop reading, which closes the upstream connection.
            await _cancel(pump)
            return
        try:
            pump.result()
        except ClientStalled:
            # Nothing more can be written to this client. Returning without the
            # final chunk makes the server drop the connection.
            return
        with contextlib.suppress(OSError):
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _end_for_shutdown(self, send: Send, complete: bool) -> None:
        record = self._record
        if not complete:
            record.outcome = usage.MIDSTREAM_ERROR
            record.error = "router shutting down"
        if not self._is_sse and not complete:
            return  # no in-band way to say it: the connection is dropped
        with contextlib.suppress(OSError, TimeoutError):
            async with asyncio.timeout(SHUTDOWN_WRITE_TIMEOUT_S):
                if not complete:
                    event = stream_error_event(MSG_SHUTTING_DOWN, CODE_SHUTTING_DOWN)
                    await send({"type": "http.response.body", "body": event, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _pump(self, send: Send) -> None:
        record = self._record
        splitter = SSESplitter() if self._is_sse else None
        try:
            try:
                async for chunk in self._upstream.aiter_bytes():
                    await self._send(send, chunk if splitter is None else self._whole_events(splitter, chunk))
                if splitter is not None:
                    events, rest = splitter.finish()
                    parts = [event for event in events if self._inspector.keep(event)]
                    if rest and self._inspector.keep(rest):
                        parts.append(rest)
                    await self._send(send, b"".join(parts))
            except _STREAM_ERRORS as exc:
                if self._is_sse and self._inspector.done_seen:
                    # [DONE] has been relayed: the client has the whole stream. A
                    # connection that breaks afterwards takes nothing away from it.
                    logger.debug("backend connection broke after [DONE]: %s", type(exc).__name__)
                    record.outcome = usage.MIDSTREAM_ERROR if self._inspector.error_seen else usage.OK
                else:
                    timed_out = isinstance(exc, httpx.TimeoutException)
                    record.outcome = usage.MIDSTREAM_ERROR
                    record.error = type(exc).__name__
                    self._on_break("midstream_timeout" if timed_out else "midstream")
                    if not self._is_sse:
                        raise UpstreamAborted(type(exc).__name__) from exc
                    # Whole events only have been sent so far; an unfinished one is
                    # dropped, so the error event starts on a clean boundary.
                    await self._send(
                        send, stream_error_event(MSG_STREAM_TIMEOUT if timed_out else MSG_STREAM_BROKEN)
                    )
            else:
                record.outcome = usage.MIDSTREAM_ERROR if self._inspector.error_seen else usage.OK
        except asyncio.CancelledError:
            if record.outcome is None:
                record.outcome = usage.CLIENT_CLOSED
            raise
        except ClientStalled:
            record.outcome = usage.CLIENT_CLOSED
            record.error = f"client took no data for {self._write_timeout_s:g} s"
            raise
        except OSError:
            # The server could not write to the client any more.
            if record.outcome is None:
                record.outcome = usage.CLIENT_CLOSED
        finally:
            if self._count_tokens:
                _set_tokens(record, self._inspector.usage)
            await self._upstream.aclose()

    def _whole_events(self, splitter: SSESplitter, chunk: bytes) -> bytes:
        parts = [event for event in splitter.feed(chunk) if self._inspector.keep(event)]
        if splitter.raw:
            parts.append(splitter.drain())
        return b"".join(parts)

    async def _send(self, send: Send, body: bytes) -> None:
        """Write to the client. A write that cannot finish means the client has stopped reading."""
        if not body:
            return
        message: Message = {"type": "http.response.body", "body": body, "more_body": True}
        if self._write_timeout_s <= 0:
            await send(message)
            return
        try:
            async with asyncio.timeout(self._write_timeout_s):
                await send(message)
        except TimeoutError:
            raise ClientStalled() from None
