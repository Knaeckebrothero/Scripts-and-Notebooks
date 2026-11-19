"""Per-key limits: requests per minute over a sliding window, and requests in flight.

Both live in memory (single process, single replica) and are keyed by the key's
database id, never by the token.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    reset: int  # epoch seconds at which the oldest counted request leaves the window

    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset),
        }


class RateLimiter:
    """Sliding 60 second window. A rejected request is not counted."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._hits: dict[int, deque[float]] = {}

    def check(self, key_id: int, rpm: int) -> RateDecision:
        now = self._clock()
        hits = self._hits.get(key_id)
        if hits is None:
            hits = self._hits[key_id] = deque()
        self._prune(hits, now)
        count = len(hits)
        if count >= rpm:
            return RateDecision(False, rpm, 0, self._reset(hits, now))
        hits.append(now)
        return RateDecision(True, rpm, max(0, rpm - count) - 1, self._reset(hits, now))

    def sweep(self) -> int:
        """Forget keys with no request in the window. Returns how many were dropped."""
        now = self._clock()
        idle = []
        for key_id, hits in self._hits.items():
            self._prune(hits, now)
            if not hits:
                idle.append(key_id)
        for key_id in idle:
            del self._hits[key_id]
        return len(idle)

    def tracked_keys(self) -> int:
        return len(self._hits)

    @staticmethod
    def _prune(hits: deque[float], now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        while hits and hits[0] <= cutoff:
            hits.popleft()

    @staticmethod
    def _reset(hits: deque[float], now: float) -> int:
        return int((hits[0] if hits else now) + WINDOW_SECONDS)


class ConcurrencyLimiter:
    """Counts requests in flight per key and enforces the key's cap, if it has one."""

    def __init__(self) -> None:
        self._in_flight: dict[int, int] = {}

    def try_acquire(self, key_id: int, cap: int | None) -> bool:
        current = self._in_flight.get(key_id, 0)
        if cap is not None and current >= cap:
            return False
        self._in_flight[key_id] = current + 1
        return True

    def release(self, key_id: int) -> None:
        current = self._in_flight.get(key_id, 0)
        if current <= 1:
            self._in_flight.pop(key_id, None)
        else:
            self._in_flight[key_id] = current - 1

    def in_flight(self, key_id: int) -> int:
        return self._in_flight.get(key_id, 0)

    def total(self) -> int:
        return sum(self._in_flight.values())
