"""The per-request record.

One is created when a request arrives and travels with it in the ASGI scope.
Handlers fill it in as they learn facts; when the response has been sent (for a
stream: when the stream has ended) it becomes the request's log line, its
metrics and its usage event. Nothing else keeps per-request state.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request

from .auth import KeyRecord

logger = logging.getLogger(__name__)

SCOPE_KEY = "model_orchestrator.record"

Cleanup = Callable[[], Awaitable[None] | None]


@dataclass
class RequestRecord:
    request_id: str
    method: str
    path: str
    started: float = field(default_factory=time.perf_counter)
    # The endpoint template ("/v1/chat/completions"), for handled paths.
    endpoint: str | None = None
    key: KeyRecord | None = None
    model: str | None = None
    route: str | None = None
    backend: str | None = None
    status: int | None = None
    # One of usage.OUTCOMES once the request reached a known key and model.
    outcome: str | None = None
    # Why the router answered without forwarding (metrics label), if it did.
    reason: str | None = None
    upstream_status: int | None = None
    stream: bool = False
    attempts: int = 0
    forwarded: bool = False
    first_byte_at: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # Set once the per-minute limiter has run; added to whatever response follows.
    rate_headers: dict[str, str] | None = None
    # Health checks and listings are logged at DEBUG only.
    quiet: bool = False
    error: str | None = None
    _cleanups: list[Cleanup] = field(default_factory=list)

    def add_cleanup(self, cleanup: Cleanup) -> None:
        """Register something to release when the request is over, whatever happened."""
        self._cleanups.append(cleanup)

    async def run_cleanups(self) -> None:
        """Release everything, in reverse order. One cleanup failing or being
        cancelled does not keep the others from running."""
        cleanups, self._cleanups = self._cleanups, []
        cancelled: asyncio.CancelledError | None = None
        for cleanup in reversed(cleanups):
            try:
                result = cleanup()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError as exc:
                cancelled = exc
            except Exception:
                logger.exception("request cleanup failed")
        if cancelled is not None:
            raise cancelled

    def duration_s(self) -> float:
        return time.perf_counter() - self.started

    def ttfb_s(self) -> float | None:
        return None if self.first_byte_at is None else self.first_byte_at - self.started

    def log_fields(self) -> dict[str, Any]:
        ttfb = self.ttfb_s()
        return {
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "outcome": self.outcome or self.reason,
            "key_id": self.key.id if self.key else None,
            "key": self.key.name if self.key else None,
            "owner": self.key.owner if self.key else None,
            "model": self.model,
            "route": self.route,
            "backend": self.backend,
            "endpoint": self.endpoint,
            "upstream_status": self.upstream_status,
            "stream": self.stream,
            "attempts": self.attempts,
            "duration_ms": round(self.duration_s() * 1000),
            "ttfb_ms": None if ttfb is None else round(ttfb * 1000),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "error": self.error,
        }


def get_record(request: Request) -> RequestRecord:
    record = request.scope.get(SCOPE_KEY)
    if not isinstance(record, RequestRecord):
        raise RuntimeError("request record missing: the request context middleware is not installed")
    return record
