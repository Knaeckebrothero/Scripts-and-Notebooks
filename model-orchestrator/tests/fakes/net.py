"""Network-level fakes: an HTTP forward proxy and a TCP relay that can be unplugged."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import ssl
import threading
import time
from dataclasses import dataclass

import aiohttp
from aiohttp import web

from .base import HOST, LoopThread

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "content-encoding", "host",
}  # fmt: skip


@dataclass
class Proxied:
    method: str
    url: str  # the absolute URL the client asked the proxy for
    headers: dict[str, str]
    status: int | None = None


class FakeForwardProxy:
    """A plain HTTP forward proxy (absolute-URI requests, no CONNECT).

    It is what a route's ``proxy:`` option points at. Every request is recorded
    and then forwarded to its real target, so a test can prove that traffic for
    a route went through the proxy.
    """

    def __init__(self, loop_thread: LoopThread) -> None:
        self._lt = loop_thread
        self.port: int | None = None
        self.requests: list[Proxied] = []
        self._runner: web.ServerRunner | None = None
        self._session: aiohttp.ClientSession | None = None

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def start(self) -> FakeForwardProxy:
        self._lt.run(self._start())
        return self

    def close(self) -> None:
        if self._runner is not None:
            self._lt.run(self._stop())

    async def _start(self) -> None:
        self._session = aiohttp.ClientSession(trust_env=False, auto_decompress=False)
        self._runner = web.ServerRunner(web.Server(self._handle), shutdown_timeout=0.2)
        await self._runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, 0))
        self.port = sock.getsockname()[1]
        await web.SockSite(self._runner, sock).start()

    async def _stop(self) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()
        if self._session is not None:
            await self._session.close()

    async def _handle(self, request: web.BaseRequest) -> web.StreamResponse:
        target = str(request.url)
        record = Proxied(request.method, target, {k.lower(): v for k, v in request.headers.items()})
        self.requests.append(record)
        if not request.url.is_absolute() or request.method == "CONNECT" or request.url.host != HOST:
            record.status = 400
            return web.Response(status=400, text="fake proxy: only absolute http://127.0.0.1 URLs")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
        assert self._session is not None
        try:
            async with self._session.request(
                request.method, target, headers=headers, data=await request.read(), allow_redirects=False
            ) as upstream:
                body = await upstream.read()
                record.status = upstream.status
                out = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
                return web.Response(status=upstream.status, body=body, headers=out)
        except aiohttp.ClientError as exc:
            record.status = 502
            return web.Response(status=502, text=f"fake proxy: {exc}")

    def calls(self, path_suffix: str | None = None) -> list[Proxied]:
        return [r for r in self.requests if path_suffix is None or r.url.split("?")[0].endswith(path_suffix)]


class TcpRelay:
    """Relays TCP connections to a fixed target and can be unplugged and plugged back in.

    The router's database URL points at the relay instead of at Postgres, so a
    test can take "the database" away from one router (``stop``) and give it
    back (``start``) without touching the server other tests are using.

    With ``tls`` the relay speaks TLS to its callers and plain TCP to the
    target: an HTTPS front for a router, the way a TLS proxy would be.
    """

    def __init__(
        self,
        loop_thread: LoopThread,
        target_host: str,
        target_port: int,
        *,
        tls: ssl.SSLContext | None = None,
    ) -> None:
        self._lt = loop_thread
        self._target = (target_host, target_port)
        self._tls = tls
        self.port: int | None = None
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> TcpRelay:
        if not self.running:
            self._lt.run(self._start())
        return self

    def stop(self) -> None:
        """Stop listening and reset every relayed connection."""
        if self.running:
            self._lt.run(self._stop())
            time.sleep(0.05)

    close = stop

    async def _start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, self.port or 0))
        self.port = sock.getsockname()[1]
        self._server = await asyncio.start_server(self._serve, sock=sock, ssl=self._tls)

    async def _stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        for writer in list(self._writers):
            with contextlib.suppress(Exception):
                writer.transport.abort()
        self._writers.clear()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(*self._target)
        except OSError:
            writer.transport.abort()
            return
        self._writers.update((writer, up_writer))

        async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (OSError, asyncio.CancelledError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    dst.close()

        await asyncio.gather(pump(reader, up_writer), pump(up_reader, writer))
        self._writers.difference_update((writer, up_writer))
