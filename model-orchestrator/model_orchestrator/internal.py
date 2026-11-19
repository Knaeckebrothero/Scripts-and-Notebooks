"""The internal listener: Prometheus metrics and the detailed health view.

No auth, on a port of its own (``ROUTER_METRICS_PORT``) that is meant for
scraping from inside the deployment and must not be published. This is where
backend URLs are visible; the public ``/health`` shows none.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Iterator
from typing import TYPE_CHECKING

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

if TYPE_CHECKING:
    from .state import RouterState

logger = logging.getLogger(__name__)


def create_internal_app(state: RouterState) -> Starlette:
    async def metrics(request: Request) -> Response:
        return Response(state.metrics.render(), media_type=state.metrics.content_type)

    async def health(request: Request) -> Response:
        return JSONResponse(await state.detailed_health())

    async def livez(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            Route("/metrics", metrics, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Route("/livez", livez, methods=["GET"]),
        ]
    )


class _Server(uvicorn.Server):
    """A uvicorn server that leaves signal handling to the main one."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def _bind(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.setblocking(False)
    except BaseException:
        sock.close()
        raise
    return sock


class InternalServer:
    def __init__(self, state: RouterState, host: str, port: int) -> None:
        self._state = state
        self._host = host
        self._port = port
        self._server: _Server | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Open the listener. If the port cannot be bound the router still serves
        its API: a missing metrics endpoint must not take every model offline."""
        try:
            sock = _bind(self._host, self._port)
        except OSError as exc:
            logger.error(
                "internal listener: cannot bind %s:%d (%s); metrics and detailed health are NOT served",
                self._host,
                self._port,
                exc.strerror or exc,
            )
            return
        config = uvicorn.Config(
            create_internal_app(self._state),
            log_config=None,
            access_log=False,
            lifespan="off",
            timeout_graceful_shutdown=2,
        )
        self._server = _Server(config)
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]), name="internal-listener")
        logger.info("internal listener (metrics, detailed health) on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._server is None or self._task is None:
            return
        self._server.should_exit = True
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            logger.warning("internal listener did not stop in time")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("internal listener ended with an error")
        self._server = None
        self._task = None
