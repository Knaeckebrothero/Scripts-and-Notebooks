"""Discovery: asking backends and upstream routers for their ``/v1/models``.

It runs after the startup probe round, whenever a backend comes back up, after
a config reload, and on each target's interval. In a pool, each backend that is
not down is asked until one answers. A backend that cannot be reached is asked
again sooner (15, 30, 60, 120 seconds) so ids appear quickly once a tunnel or a
slow-starting server is there; that also covers backends without a health probe.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from .clients import ClientPool
from .health import HealthMonitor
from .registry import ClientKey, Registry, Target

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 5.0
RETRY_LADDER_S = (15.0, 30.0, 60.0, 120.0)

_FetchKey = tuple[str, ClientKey, str | None]


@dataclass(frozen=True)
class Listing:
    """The result of asking one backend for its models."""

    # The backend sent an HTTP response (of any status).
    answered: bool
    # The ``data`` list of a 200 answer; None when there is no usable listing.
    models: list[Any] | None = None
    error: str | None = None


async def fetch_models(client: httpx.AsyncClient, base_url: str, api_key: str | None) -> Listing:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = await client.get(f"{base_url}/v1/models", headers=headers, timeout=FETCH_TIMEOUT_S)
    except httpx.HTTPError as exc:
        return Listing(answered=False, error=type(exc).__name__)
    except Exception as exc:  # e.g. an unusable URL: report, never raise into the loop
        return Listing(answered=False, error=f"{type(exc).__name__}: {exc}"[:200])
    if response.status_code != 200:
        return Listing(answered=True, error=f"HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        return Listing(answered=True, error="response is not JSON")
    data = body.get("data") if isinstance(body, dict) else None
    if data is None:
        data = []
    if not isinstance(data, list):
        return Listing(answered=True, error="'data' is not a list")
    return Listing(answered=True, models=data)


def retry_delay(failures: int, interval: float) -> float:
    """Seconds until the next attempt after ``failures`` unanswered ones in a row."""
    if failures <= 0:
        return interval
    return min(interval, RETRY_LADDER_S[min(failures, len(RETRY_LADDER_S)) - 1])


class Discoverer:
    def __init__(
        self,
        registry: Registry,
        monitor: HealthMonitor,
        clients: ClientPool,
        *,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._registry = registry
        self._monitor = monitor
        self._clients = clients
        self._on_change = on_change
        self._lock = asyncio.Lock()
        self._due: dict[tuple[str, str], float] = {}
        self._wake = asyncio.Event()

    def trigger(self, targets: Sequence[Target] | None = None) -> None:
        """Make targets due now (all of them when none are named)."""
        for target in targets if targets is not None else self._registry.targets:
            self._due[target.ident] = 0.0
        self._wake.set()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            targets = self._registry.targets
            idents = {t.ident for t in targets}
            self._due = {ident: due for ident, due in self._due.items() if ident in idents}
            due = [t for t in targets if self._due.get(t.ident, 0.0) <= now]
            if due:
                try:
                    await self.discover(due)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("discovery failed")
                    for target in due:
                        self._due[target.ident] = loop.time() + RETRY_LADDER_S[0]
                continue
            delay = min((self._due.get(t.ident, now) - now for t in targets), default=60.0)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(0.05, delay))
            except TimeoutError:
                pass
            self._wake.clear()

    async def discover(self, targets: Sequence[Target]) -> bool:
        """Ask the given targets once. Returns True when the model table changed."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            cache: dict[_FetchKey, asyncio.Task[Listing]] = {}
            results = await asyncio.gather(*(self._ask(target, cache) for target in targets))
            changed = False
            for target, listing in zip(targets, results, strict=True):
                if not self._registry.is_current(target):
                    # Replaced by a reload while we were asking; the new one is asked next.
                    continue
                state = self._registry.discovery_state(target)
                if listing is None:
                    # Every backend is down: the probe that sees one come back triggers us.
                    self._due[target.ident] = loop.time() + target.discovery_interval
                    continue
                if listing.models is not None:
                    if self._registry.apply_listing(target, listing.models):
                        changed = True
                else:
                    self._registry.note_no_listing(
                        target, listing.error or "no listing", answered=listing.answered
                    )
                    logger.info(
                        "discovery: %s '%s' gave no model list (%s)", target.kind, target.name, listing.error
                    )
                self._due[target.ident] = loop.time() + retry_delay(state.failures, target.discovery_interval)
        if changed:
            table = self._registry.table
            logger.info("model table rebuilt: %d model id(s): %s", len(table.entries), sorted(table.entries))
            if self._on_change is not None:
                self._on_change()
        return changed

    async def _ask(self, target: Target, cache: dict[_FetchKey, asyncio.Task[Listing]]) -> Listing | None:
        last: Listing | None = None
        for backend in self._monitor.candidates(target):
            key: _FetchKey = (backend, target.client_key, target.api_key)
            task = cache.get(key)
            if task is None:
                client = self._clients.control(target.client_key)
                task = cache[key] = asyncio.ensure_future(fetch_models(client, backend, target.api_key))
            last = await task
            if last.answered:
                return last
        return last
