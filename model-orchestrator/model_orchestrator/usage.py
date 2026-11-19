"""Usage accounting: one event per request, written off the request path.

Two tables:

* ``usage_daily`` keeps its exact meaning from the previous router: one
  increment per *forwarded* request, ``model`` is the route name, and
  ``error_count`` covers every forwarded outcome except ``ok`` and
  ``client_closed``.
* ``usage_hourly`` records every outcome, including requests that were not
  forwarded (rate limit, concurrency cap, unavailable model), with token counts
  and the model id as the client sent it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .metrics import Metrics

logger = logging.getLogger(__name__)

OK = "ok"
CLIENT_ERROR = "client_error"
BACKEND_ERROR = "backend_error"
UNREACHABLE = "unreachable"
TIMEOUT = "timeout"
MIDSTREAM_ERROR = "midstream_error"
CLIENT_CLOSED = "client_closed"
UNAVAILABLE = "unavailable"
RATE_LIMITED = "rate_limited"
CONCURRENCY_LIMITED = "concurrency_limited"

FORWARDED_OUTCOMES = frozenset(
    {OK, CLIENT_ERROR, BACKEND_ERROR, UNREACHABLE, TIMEOUT, MIDSTREAM_ERROR, CLIENT_CLOSED}
)
OUTCOMES = FORWARDED_OUTCOMES | {UNAVAILABLE, RATE_LIMITED, CONCURRENCY_LIMITED}
# Forwarded outcomes that usage_daily does not count as an error.
_DAILY_NOT_AN_ERROR = frozenset({OK, CLIENT_CLOSED})

LAST_USED_MIN_INTERVAL_S = 60.0


@dataclass(frozen=True)
class UsageEvent:
    at: datetime  # when the request ended, timezone-aware
    key_id: int
    owner: str
    model: str  # the model id as the client sent it
    route: str  # the route name
    outcome: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0


@dataclass
class DailyRow:
    owner: str
    requests: int = 0
    errors: int = 0


@dataclass
class HourlyRow:
    owner: str
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0


@dataclass
class UsageBatch:
    # (key id, route name)
    daily: dict[tuple[int, str], DailyRow] = field(default_factory=dict)
    # (hour in UTC, key id, model, route, outcome)
    hourly: dict[tuple[datetime, int, str, str, str], HourlyRow] = field(default_factory=dict)
    # Keys with at least one forwarded request (candidates for last_used_at).
    forwarded_key_ids: set[int] = field(default_factory=set)
    events: int = 0


def aggregate(events: Iterable[UsageEvent]) -> UsageBatch:
    batch = UsageBatch()
    for event in events:
        batch.events += 1
        if event.outcome in FORWARDED_OUTCOMES:
            daily = batch.daily.setdefault((event.key_id, event.route), DailyRow(owner=event.owner))
            daily.owner = event.owner
            daily.requests += 1
            if event.outcome not in _DAILY_NOT_AN_ERROR:
                daily.errors += 1
            batch.forwarded_key_ids.add(event.key_id)
        hour = event.at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        hourly = batch.hourly.setdefault(
            (hour, event.key_id, event.model, event.route, event.outcome), HourlyRow(owner=event.owner)
        )
        hourly.owner = event.owner
        hourly.requests += 1
        hourly.prompt_tokens += event.prompt_tokens
        hourly.completion_tokens += event.completion_tokens
        hourly.duration_ms += event.duration_ms
    return batch


WriteBatch = Callable[[UsageBatch, list[int]], Awaitable[None]]


class UsageWriter:
    """Queue plus background writer.

    ``record`` never blocks and never raises. A batch that cannot be written is
    retried a few times with backoff and then dropped; drops are counted. ``stop``
    flushes what is queued.
    """

    def __init__(
        self,
        write: WriteBatch,
        *,
        metrics: Metrics | None = None,
        max_queue: int = 10_000,
        max_batch: int = 2_000,
        retry_delays_s: tuple[float, ...] = (0.5, 2.0, 5.0),
    ) -> None:
        self._write = write
        self._metrics = metrics
        self._max_queue = max_queue
        self._max_batch = max_batch
        self._retry_delays = retry_delays_s
        self._queue: deque[UsageEvent] = deque()
        self._wake = asyncio.Event()
        self._stopping = False
        self._last_touched: dict[int, float] = {}
        self._last_drop_log = float("-inf")
        self.dropped = 0

    def depth(self) -> int:
        return len(self._queue)

    def record(self, event: UsageEvent) -> bool:
        if len(self._queue) >= self._max_queue:
            self._drop(1, "queue full")
            return False
        self._queue.append(event)
        self._wake.set()
        return True

    async def run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._queue:
                size = min(len(self._queue), self._max_batch)
                await self._write_batch([self._queue.popleft() for _ in range(size)])
            if self._stopping:
                return

    async def stop(self, task: asyncio.Task[None] | None, *, timeout_s: float = 10.0) -> None:
        """Flush the queue (one attempt per batch) and end the writer."""
        self._stopping = True
        self._wake.set()
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=timeout_s)
        except TimeoutError:
            logger.warning("usage writer did not flush in %.0f s; %d record(s) lost", timeout_s, self.depth())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("usage writer ended with an error")

    async def _write_batch(self, events: list[UsageEvent]) -> None:
        batch = aggregate(events)
        now = time.monotonic()
        touch = sorted(
            key_id
            for key_id in batch.forwarded_key_ids
            if now - self._last_touched.get(key_id, float("-inf")) >= LAST_USED_MIN_INTERVAL_S
        )
        attempt = 0
        while True:
            try:
                await self._write(batch, touch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._metrics is not None:
                    self._metrics.usage_write_failures.inc()
                if self._stopping or attempt >= len(self._retry_delays):
                    self._drop(len(events), f"write failed: {type(exc).__name__}: {exc}")
                    return
                await asyncio.sleep(self._retry_delays[attempt])
                attempt += 1
                continue
            for key_id in touch:
                self._last_touched[key_id] = now
            return

    def _drop(self, count: int, why: str) -> None:
        self.dropped += count
        if self._metrics is not None:
            self._metrics.usage_dropped.inc(count)
        now = time.monotonic()
        if now - self._last_drop_log >= 10.0:
            self._last_drop_log = now
            logger.warning("usage: dropping %d record(s), %s", count, " ".join(why.split())[:300])
