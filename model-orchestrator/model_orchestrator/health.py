"""Backend health: active probes, passive marks, and backend selection.

Two separate signals, on purpose:

* **Active probes** decide availability. A backend is *down* after
  ``ROUTER_HEALTH_FAILS`` consecutive failed probes and *up* again after one
  success. A model whose route has no backend left that is not down is
  unavailable. ``ROUTER_HEALTH_GATING=off`` keeps probing and reporting but
  stops this from affecting requests.
* **Passive marks** only steer selection. A connect error, timeout or 5xx on a
  real request marks the backend for ``ROUTER_UNHEALTHY_SECONDS``; unmarked
  backends of a pool are preferred, and if all are marked all are used. A mark
  never makes a model unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

import httpx

from .clients import ClientPool
from .metrics import Metrics
from .registry import ClientKey, Target

logger = logging.getLogger(__name__)

UP = "up"
DOWN = "down"
UNKNOWN = "unknown"
UNPROBED = "unprobed"

# (backend URL, health path, client key): one probe, however many routes share the backend.
ProbeKey = tuple[str, str, ClientKey]

# A probe asked for by a failed request is not repeated more often than this.
_MIN_PROBE_SPACING_S = 1.0


@dataclass
class BackendState:
    status: str = UNKNOWN
    consecutive_failures: int = 0
    last_probe_at: float | None = None
    last_error: str | None = None
    last_duration_s: float | None = None
    changed_at: float | None = None

    def record(self, ok: bool, threshold: int) -> tuple[str, str]:
        """Apply one probe result. Returns (status before, status after).

        The first result for a backend is taken at face value, so a backend that
        is dead when the router starts (or when it is added) is down at once.
        """
        before = self.status
        if ok:
            self.consecutive_failures = 0
            self.status = UP
        else:
            self.consecutive_failures += 1
            if before == UNKNOWN or self.consecutive_failures >= threshold:
                self.status = DOWN
        if self.status != before:
            self.changed_at = time.time()
        return before, self.status


class HealthMonitor:
    """Probe state, passive marks and round-robin selection. No I/O."""

    def __init__(
        self,
        *,
        fails: int,
        unhealthy_seconds: float,
        gating: bool,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fails = fails
        self._unhealthy_seconds = unhealthy_seconds
        self.gating = gating
        self._clock = clock
        self._states: dict[ProbeKey, BackendState] = {}
        self._probe_api_key: dict[ProbeKey, str | None] = {}
        self._users: dict[ProbeKey, list[Target]] = {}
        self._passive_until: dict[str, float] = {}
        self._round_robin: dict[tuple[str, str], int] = {}

    # -- configuration ----------------------------------------------------

    def configure(self, targets: Sequence[Target]) -> list[ProbeKey]:
        """Set the probes for a config. State of probes that stay is kept.

        Returns the probes that are new (their state is unknown until probed).
        """
        states: dict[ProbeKey, BackendState] = {}
        api_keys: dict[ProbeKey, str | None] = {}
        users: dict[ProbeKey, list[Target]] = {}
        for target in targets:
            for backend in target.backends:
                key = self.probe_key(target, backend)
                if key is None:
                    continue
                states.setdefault(key, self._states.get(key) or BackendState())
                api_keys.setdefault(key, target.api_key)
                users.setdefault(key, []).append(target)
        fresh = [key for key in states if key not in self._states]
        self._states, self._probe_api_key, self._users = states, api_keys, users
        idents = {target.ident for target in targets}
        self._round_robin = {k: v for k, v in self._round_robin.items() if k in idents}
        return fresh

    @staticmethod
    def probe_key(target: Target, backend: str) -> ProbeKey | None:
        if not target.health_path:
            return None
        return (backend, target.health_path, target.client_key)

    def probe_keys(self) -> list[ProbeKey]:
        return list(self._states)

    def unknown_keys(self) -> list[ProbeKey]:
        return [key for key, state in self._states.items() if state.status == UNKNOWN]

    def api_key_for(self, key: ProbeKey) -> str | None:
        return self._probe_api_key.get(key)

    def users_of(self, key: ProbeKey) -> list[Target]:
        return list(self._users.get(key, ()))

    def state(self, key: ProbeKey) -> BackendState | None:
        return self._states.get(key)

    # -- probe results ----------------------------------------------------

    def record_probe(
        self, key: ProbeKey, ok: bool, *, error: str | None = None, duration_s: float | None = None
    ) -> tuple[str, str]:
        state = self._states.get(key)
        if state is None:
            return UNKNOWN, UNKNOWN
        state.last_probe_at = time.time()
        state.last_error = None if ok else error
        state.last_duration_s = duration_s
        return state.record(ok, self._fails)

    # -- availability -----------------------------------------------------

    def status(self, target: Target, backend: str) -> str:
        key = self.probe_key(target, backend)
        if key is None:
            return UNPROBED
        state = self._states.get(key)
        return state.status if state else UNKNOWN

    def target_up(self, target: Target) -> bool:
        """At least one backend that is not known to be down (ignores the gating switch)."""
        return any(self.status(target, backend) != DOWN for backend in target.backends)

    def available(self, target: Target) -> bool:
        return not self.gating or self.target_up(target)

    def candidates(self, target: Target) -> list[str]:
        """The backends a request may be sent to."""
        if not self.gating:
            return list(target.backends)
        return [backend for backend in target.backends if self.status(target, backend) != DOWN]

    # -- selection ----------------------------------------------------------

    def select(self, target: Target, exclude: Collection[str] = ()) -> str | None:
        """Round-robin over the candidates, preferring ones without a passive mark.

        ``exclude`` holds the backends this request has already tried. A retry
        does not advance the rotation, so first attempts keep alternating evenly.
        """
        pool = [backend for backend in self.candidates(target) if backend not in exclude]
        if not pool:
            return None
        now = self._clock()
        unmarked = [backend for backend in pool if self._passive_until.get(backend, 0.0) <= now]
        choice = unmarked or pool
        index = self._round_robin.get(target.ident, 0)
        if not exclude:
            self._round_robin[target.ident] = index + 1
        return choice[index % len(choice)]

    def mark_passive(self, backend: str) -> None:
        if self._unhealthy_seconds > 0:
            self._passive_until[backend] = self._clock() + self._unhealthy_seconds

    def passive_remaining(self, backend: str) -> float:
        return max(0.0, self._passive_until.get(backend, 0.0) - self._clock())

    def prune_passive(self) -> None:
        now = self._clock()
        self._passive_until = {b: until for b, until in self._passive_until.items() if until > now}


class HealthProber:
    """Runs the probes: one round per interval, plus probes asked for by failed requests."""

    def __init__(
        self,
        monitor: HealthMonitor,
        clients: ClientPool,
        *,
        interval_s: float,
        timeout_s: float,
        metrics: Metrics | None = None,
        on_recovered: Callable[[list[Target]], None] | None = None,
    ) -> None:
        self._monitor = monitor
        self._clients = clients
        self._interval = interval_s
        self._timeout = timeout_s
        self._metrics = metrics
        self._on_recovered = on_recovered
        self._requested: set[ProbeKey] = set()
        self._wake = asyncio.Event()
        self._last_started: dict[ProbeKey, float] = {}

    def request_probe(self, target: Target, backend: str) -> None:
        """Ask for a probe now (a real request just failed to connect)."""
        key = self._monitor.probe_key(target, backend)
        if key is not None and self._monitor.state(key) is not None:
            self._requested.add(key)
            self._wake.set()

    async def probe_all(self) -> None:
        await self._probe(self._monitor.probe_keys())

    async def probe_unknown(self) -> None:
        await self._probe(self._monitor.unknown_keys())

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        next_round = loop.time() + self._interval
        while True:
            delay = max(0.0, next_round - loop.time())
            if self._requested:
                delay = min(delay, _MIN_PROBE_SPACING_S)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                if loop.time() >= next_round:
                    next_round = loop.time() + self._interval
                    self._requested.clear()
                    await self.probe_all()
                else:
                    now = loop.time()
                    due = [
                        key
                        for key in self._requested
                        if now - self._last_started.get(key, float("-inf")) >= _MIN_PROBE_SPACING_S
                    ]
                    self._requested.difference_update(due)
                    await self._probe(due)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("health probe round failed")

    async def _probe(self, keys: Sequence[ProbeKey]) -> None:
        if not keys:
            return
        recovered: list[Target] = []
        loop = asyncio.get_running_loop()
        for key in keys:
            self._last_started[key] = loop.time()
        results = await asyncio.gather(*(self._probe_one(key) for key in keys))
        for key, (ok, error, duration) in zip(keys, results, strict=True):
            before, after = self._monitor.record_probe(key, ok, error=error, duration_s=duration)
            if self._metrics is not None:
                self._metrics.probe_duration.labels(key[0], "ok" if ok else "fail").observe(duration)
            if before != after:
                users = self._monitor.users_of(key)
                names = ", ".join(sorted({t.name for t in users})) or "-"
                if after == DOWN:
                    logger.warning("backend down: %s%s (%s) [%s]", key[0], key[1], error, names)
                else:
                    logger.info("backend up: %s%s [%s]", key[0], key[1], names)
                if after == UP and before == DOWN:
                    recovered.extend(users)
        self._last_started = {
            k: v for k, v in self._last_started.items() if self._monitor.state(k) is not None
        }
        if recovered and self._on_recovered is not None:
            self._on_recovered(list({id(t): t for t in recovered}.values()))

    async def _probe_one(self, key: ProbeKey) -> tuple[bool, str | None, float]:
        """One probe. Success is any HTTP status below 500."""
        backend, path, client_key = key
        headers = {}
        api_key = self._monitor.api_key_for(key)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        started = time.perf_counter()
        try:
            # A pool of its own: however much traffic is in flight, a probe gets a connection.
            client = self._clients.control(client_key)
            async with asyncio.timeout(self._timeout):
                async with client.stream(
                    "GET", f"{backend}{path}", headers=headers, timeout=httpx.Timeout(self._timeout)
                ) as response:
                    status = response.status_code
        except TimeoutError:
            return False, "timeout", time.perf_counter() - started
        except httpx.TimeoutException:
            return False, "timeout", time.perf_counter() - started
        except httpx.HTTPError as exc:
            return False, type(exc).__name__, time.perf_counter() - started
        except Exception as exc:  # a broken client or URL must not stop the probe loop
            return False, f"{type(exc).__name__}: {exc}"[:200], time.perf_counter() - started
        duration = time.perf_counter() - started
        if status >= 500:
            return False, f"HTTP {status}", duration
        return True, None, duration
