"""Machinery shared by every fake backend.

A fake is a real HTTP server on 127.0.0.1 (aiohttp), hosted on one background
event loop that lives for the whole test session. Tests run in the main thread
and talk to a fake through plain attributes and methods:

* everything a fake receives is appended to ``fake.requests`` as a
  :class:`Recorded` (headers, raw body, parsed JSON, multipart fields, what the
  fake answered, and whether the caller went away before the answer was out);
* faults are injected with ``delay`` / ``fail`` / ``hang`` / ``drop`` /
  ``reset_connection`` / ``health_status`` / ``stop`` / ``tarpit``.

Faults match a request path. ``path=None`` means "every endpoint that does
work", that is everything except ``/health`` and ``/v1/models``; name those two
explicitly to break them. ``times=n`` limits a fault to n requests; several
faults queue up, one per request (delays stack on top of whatever comes next).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import socket
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

HOST = "127.0.0.1"
_PASSIVE_PATHS = ("/health", "/v1/models")


class LoopThread:
    """One asyncio loop in a daemon thread. Every fake server runs on it."""

    def __init__(self) -> None:
        # Cut connections and cancelled handlers are the point of these fakes, not errors.
        logging.getLogger("aiohttp.server").setLevel(logging.CRITICAL)
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="fakes-loop", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout: float = 15.0):
        """Run a coroutine on the fakes loop from the test thread and wait for it."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def call(self, fn, *args) -> None:
        self.loop.call_soon_threadsafe(fn, *args)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)


@dataclass
class FormField:
    """One part of a multipart/form-data request, in the order received."""

    name: str
    value: str | None = None  # text fields
    filename: str | None = None  # file fields
    content_type: str | None = None
    size: int = 0
    sha256: str | None = None

    def as_dict(self) -> dict[str, Any]:
        if self.filename is None:
            return {"name": self.name, "value": self.value}
        return {
            "name": self.name,
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass
class Recorded:
    """One request as the fake saw it, and what became of it."""

    seq: int
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]  # header names lower-cased
    body: bytes
    json: Any = None
    form: list[FormField] | None = None
    started: float = 0.0
    finished: float | None = None
    status: int | None = None  # status the fake answered with, if it got that far
    sent: bytes = b""  # response body bytes the fake actually wrote
    completed: bool = False  # the full response was written
    aborted: bool = False  # the caller closed the connection before that
    dropped: bool = False  # the fake itself cut the connection (fault injection)

    def form_values(self, name: str) -> list[str | None]:
        return [f.value for f in (self.form or []) if f.name == name]

    def form_file(self, name: str = "file") -> FormField | None:
        return next((f for f in (self.form or []) if f.name == name and f.filename is not None), None)


@dataclass
class _Fault:
    kind: str  # delay | status | hang | drop | reset
    path: str | None
    times: int | None  # how many more requests it applies to; None = until cleared
    seconds: float = 0.0
    status: int = 500
    body: bytes = b""
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)
    after: int = 1
    rst: bool = False

    def matches(self, path: str) -> bool:
        if self.path is None:
            return path not in _PASSIVE_PATHS
        return path == self.path


# The request being handled and an armed drop fault, for the handler that runs under the middleware.
_REC: ContextVar[Recorded] = ContextVar("fake_recorded")
_DROP: ContextVar[_Fault | None] = ContextVar("fake_drop", default=None)


def current() -> Recorded:
    """The :class:`Recorded` of the request this handler is serving."""
    return _REC.get()


def parse_multipart(content_type: str, body: bytes) -> list[FormField]:
    """Split a multipart/form-data body into its parts. Binary safe, order kept."""
    boundary = None
    for piece in content_type.split(";"):
        piece = piece.strip()
        if piece.lower().startswith("boundary="):
            boundary = piece.split("=", 1)[1].strip('"')
    if not boundary:
        return []
    delim = b"--" + boundary.encode()
    fields: list[FormField] = []
    for raw in body.split(delim)[1:]:
        if raw.startswith(b"--"):
            break  # closing delimiter
        raw = raw.removeprefix(b"\r\n")
        head, sep, content = raw.partition(b"\r\n\r\n")
        if not sep:
            continue
        content = content.removesuffix(b"\r\n")
        name = filename = ctype = None
        for line in head.decode("utf-8", "replace").split("\r\n"):
            key, _, val = line.partition(":")
            if key.strip().lower() == "content-disposition":
                for param in val.split(";"):
                    k, _, v = param.strip().partition("=")
                    if k == "name":
                        name = v.strip('"')
                    elif k == "filename":
                        filename = v.strip('"')
            elif key.strip().lower() == "content-type":
                ctype = val.strip()
        if name is None:
            continue
        if filename is None:
            fields.append(FormField(name=name, value=content.decode("utf-8", "replace")))
        else:
            fields.append(
                FormField(
                    name=name,
                    filename=filename,
                    content_type=ctype,
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
    return fields


def dumps(obj: Any) -> str:
    """Compact JSON with raw (unescaped) non-ASCII, the way vLLM writes it."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class FakeBackend:
    """Base class: lifecycle, recording, fault injection. Subclasses add endpoints."""

    kind = "base"

    def __init__(self, loop_thread: LoopThread, name: str | None = None) -> None:
        self._lt = loop_thread
        self.name = name or self.kind
        # Tests leave both alone: loopback, and a free port picked at the first start.
        # Set them before start() to serve a fixed address (scripts/demo_backends.py).
        self.host = HOST
        self.port: int | None = None
        self.requests: list[Recorded] = []
        self.health_status = 200
        self._runner: web.AppRunner | None = None
        self._faults: list[_Fault] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._stopping = False
        self._tarpit: list[socket.socket] = []
        self._gate: asyncio.Event | None = None
        self._pause_after: int | None = None

    # ---- lifecycle -------------------------------------------------------

    @property
    def url(self) -> str:
        assert self.port is not None, "fake was never started"
        return f"http://{self.host}:{self.port}"

    @property
    def running(self) -> bool:
        return self._runner is not None

    def start(self) -> FakeBackend:
        if self._tarpit:
            self.untarpit()
        if not self.running:
            self._lt.run(self._start())
        return self

    def stop(self) -> None:
        """Stop listening and cut every open connection: callers get 'connection refused'."""
        if self.running:
            self._lt.run(self._stop())

    def close(self) -> None:
        self.untarpit()
        self.stop()

    def _bind(self, backlog: int | None = None) -> socket.socket:
        """Bind the fake's port: a free one the first time, the same one on every restart."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        deadline = time.monotonic() + 2.0
        while True:
            try:
                sock.bind((self.host, self.port or 0))
                break
            except OSError:
                # Rare: while the fake was down, an outgoing connection borrowed its port number.
                if self.port is None or time.monotonic() >= deadline:
                    sock.close()
                    raise
                time.sleep(0.05)
        self.port = sock.getsockname()[1]
        if backlog is not None:
            sock.listen(backlog)
        return sock

    async def _start(self) -> None:
        app = web.Application(middlewares=[self._middleware], client_max_size=1024**3)
        app.router.add_get("/health", self._health)
        self._add_routes(app.router)
        app.router.add_route("*", "/{tail:.*}", self._not_found)
        runner = web.AppRunner(app, handler_cancellation=True, access_log=None, shutdown_timeout=0.2)
        await runner.setup()
        await web.SockSite(runner, self._bind()).start()
        self._gate = asyncio.Event()
        self._runner = runner

    async def _stop(self) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            self._stopping = True
            try:
                if self._gate is not None:
                    self._gate.set()
                await runner.cleanup()
            finally:
                self._stopping = False

    def tarpit(self) -> None:
        """Turn the port into a black hole: new TCP connections hang in connect().

        The server is stopped and replaced by a socket that listens with a
        backlog of zero and never accepts. Once its accept queue is full the
        kernel silently drops further SYNs, which is the closest thing to an
        unreachable host that loopback offers.
        """
        self.stop()
        if self._tarpit:
            return
        listener = self._bind(backlog=0)
        self._tarpit = [listener]
        for _ in range(4):  # fill the accept queue; the surplus just hangs too
            filler = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            filler.setblocking(False)
            with contextlib.suppress(BlockingIOError, OSError):
                filler.connect((HOST, self.port))
            self._tarpit.append(filler)
        time.sleep(0.05)

    def untarpit(self) -> None:
        for sock in reversed(self._tarpit):
            with contextlib.suppress(OSError):
                sock.close()
        self._tarpit = []

    # ---- fault injection -------------------------------------------------

    def delay(self, seconds: float, *, path: str | None = None, times: int | None = None) -> None:
        """Answer normally, but only after ``seconds``."""
        self._add(_Fault("delay", path, times, seconds=seconds))

    def fail(
        self,
        status: int,
        body: Any = None,
        *,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
        path: str | None = None,
        times: int | None = None,
    ) -> bytes:
        """Answer with ``status`` and ``body`` (dict/list -> JSON, str/bytes -> as is).

        Returns the exact body bytes the fake will send.
        """
        if body is None:
            body = {
                "error": {"message": f"injected {status}", "type": "FakeError", "param": None, "code": status}
            }
        if isinstance(body, (dict, list)):
            raw, ctype = dumps(body).encode(), "application/json"
        elif isinstance(body, str):
            raw, ctype = body.encode(), "text/plain; charset=utf-8"
        else:
            raw, ctype = bytes(body), "application/octet-stream"
        self._add(
            _Fault(
                "status", path, times, status=status, body=raw,
                content_type=content_type or ctype, headers=dict(headers or {}),
            )
        )  # fmt: skip
        return raw

    def hang(self, *, path: str | None = None, times: int | None = None) -> None:
        """Read the request, then never answer."""
        self._add(_Fault("hang", path, times))

    def drop(
        self, *, after: int = 1, rst: bool = False, path: str | None = None, times: int | None = None
    ) -> None:
        """Cut the connection mid-response.

        Streams: after ``after`` chunks were written. Plain responses: after the
        headers and half of the body. ``rst=True`` resets instead of closing.
        """
        self._add(_Fault("drop", path, times, after=after, rst=rst))

    def reset_connection(self, *, path: str | None = None, times: int | None = None) -> None:
        """Read the request, then close the connection without a single response byte."""
        self._add(_Fault("reset", path, times))

    def clear_faults(self) -> None:
        with self._lock:
            self._faults.clear()

    def _add(self, fault: _Fault) -> None:
        with self._lock:
            self._faults.append(fault)

    def _take(self, path: str) -> list[_Fault]:
        """The faults for one request: every matching delay, plus the oldest other fault.

        So ``fail(400, times=1); fail(500, times=1)`` is a queue: the first
        request gets the 400 and the second the 500.
        """
        taken: list[_Fault] = []
        with self._lock:
            for fault in list(self._faults):
                if not fault.matches(path):
                    continue
                if fault.kind != "delay" and any(t.kind != "delay" for t in taken):
                    continue
                taken.append(fault)
                if fault.times is not None:
                    fault.times -= 1
                    if fault.times <= 0:
                        self._faults.remove(fault)
        return taken

    def pause_stream_after(self, chunks: int) -> None:
        """Streams stop after ``chunks`` chunks until :meth:`resume_stream` is called."""
        self._pause_after = chunks
        if self._gate is not None:
            self._lt.call(self._gate.clear)

    def resume_stream(self) -> None:
        self._pause_after = None
        if self._gate is not None:
            self._lt.call(self._gate.set)

    # ---- recording -------------------------------------------------------

    def calls(self, path: str | None = None, method: str | None = None) -> list[Recorded]:
        with self._lock:
            return [
                r
                for r in self.requests
                if (path is None or r.path == path) and (method is None or r.method == method)
            ]

    def last(self, path: str | None = None) -> Recorded:
        found = self.calls(path)
        assert found, f"fake {self.name!r} received no request for {path or 'any path'}"
        return found[-1]

    def clear_requests(self) -> None:
        with self._lock:
            self.requests.clear()

    def reset(self) -> None:
        """Back to a healthy, empty fake. Called between tests for shared fakes."""
        self.clear_faults()
        self.health_status = 200
        self.resume_stream()
        self._reset_state()
        if not self.running or self._tarpit:
            self.start()
        self.clear_requests()

    def _reset_state(self) -> None:  # subclasses restore their own defaults
        pass

    # ---- request handling ------------------------------------------------

    def _add_routes(self, router: web.UrlDispatcher) -> None:
        raise NotImplementedError

    async def _health(self, request: web.Request) -> web.StreamResponse:
        return await self._reply(request, self.health_status, b"", "text/plain")

    async def _not_found(self, request: web.Request) -> web.StreamResponse:
        return await self._reply(request, 404, b'{"detail":"Not Found"}', "application/json")

    @web.middleware
    async def _middleware(self, request: web.Request, handler):
        body = await request.read()
        ctype = request.headers.get("Content-Type", "")
        with self._lock:
            self._seq += 1
            rec = Recorded(
                seq=self._seq,
                method=request.method,
                path=request.path,
                query=dict(request.query),
                headers={k.lower(): v for k, v in request.headers.items()},
                body=body,
                started=time.monotonic(),
            )
            self.requests.append(rec)
        if ctype.startswith("multipart/form-data"):
            rec.form = parse_multipart(ctype, body)
        elif body:
            with contextlib.suppress(ValueError):
                rec.json = json.loads(body)
        _REC.set(rec)
        _DROP.set(None)
        try:
            return await self._dispatch(request, handler, rec)
        except asyncio.CancelledError:
            rec.aborted = not self._stopping and not rec.dropped
            raise
        except ConnectionError:
            rec.aborted = not self._stopping and not rec.dropped
            raise
        finally:
            rec.finished = time.monotonic()

    async def _dispatch(self, request: web.Request, handler, rec: Recorded) -> web.StreamResponse:
        faults = self._take(request.path)
        for fault in faults:
            if fault.kind == "delay":
                await asyncio.sleep(fault.seconds)
        for fault in faults:
            if fault.kind == "hang":
                await asyncio.Event().wait()  # until the caller gives up or the fake stops
            if fault.kind == "reset":
                rec.dropped = True
                if request.transport is not None:
                    request.transport.abort()
                return web.Response(status=599)  # never reaches the wire: the transport is gone
            if fault.kind == "status":
                return await self._reply(request, fault.status, fault.body, fault.content_type, fault.headers)
            if fault.kind == "drop":
                _DROP.set(fault)
        return await handler(request)

    async def _reply(
        self,
        request: web.Request,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> web.StreamResponse:
        """Send a complete response in one go (or half of it, when a drop fault is armed)."""
        rec: Recorded = current()
        fault: _Fault | None = _DROP.get()
        if fault is not None:
            return await self._reply_truncated(request, status, body, content_type, fault)
        rec.status, rec.sent, rec.completed = status, body, True
        return web.Response(
            status=status, body=body, headers={"Content-Type": content_type, **(headers or {})}
        )

    async def _reply_truncated(self, request, status, body, content_type, fault: _Fault):
        rec: Recorded = current()
        resp = web.StreamResponse(
            status=status, headers={"Content-Type": content_type, "Content-Length": str(len(body))}
        )
        await resp.prepare(request)
        rec.status = status
        half = body[: len(body) // 2]
        await resp.write(half)
        rec.sent = half
        await self._cut(request, fault)
        return resp

    async def _cut(self, request: web.Request, fault: _Fault) -> None:
        rec: Recorded = current()
        rec.dropped = True
        await asyncio.sleep(0.05)  # let what was written reach the peer first
        if request.transport is not None:
            if fault.rst:
                request.transport.abort()
            else:
                request.transport.close()

    async def _stream(
        self,
        request: web.Request,
        chunks: list[bytes],
        *,
        gap: float = 0.0,
        content_type: str = "text/event-stream; charset=utf-8",
    ) -> web.StreamResponse:
        """Write ``chunks`` one by one as a chunked response, honouring drop and pause."""
        rec: Recorded = current()
        fault: _Fault | None = _DROP.get()
        resp = web.StreamResponse(
            status=200, headers={"Content-Type": content_type, "Cache-Control": "no-cache"}
        )
        await resp.prepare(request)
        rec.status = 200
        for i, chunk in enumerate(chunks):
            if fault is not None and i >= fault.after:
                await self._cut(request, fault)
                return resp
            if self._pause_after is not None and i >= self._pause_after and self._gate is not None:
                await self._gate.wait()
            await resp.write(chunk)
            rec.sent += chunk
            if gap:
                await asyncio.sleep(gap)
        await resp.write_eof()
        rec.completed = True
        return resp
