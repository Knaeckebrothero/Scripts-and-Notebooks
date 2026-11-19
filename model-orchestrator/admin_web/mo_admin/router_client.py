"""Live status from the router's public endpoints.

``GET /health`` exists in two shapes and both are accepted:

* older routers report per backend URL::

      {"status", "router", "database": {...},
       "backends": {"<url>": "healthy|unhealthy|unavailable|skipped"}}

* newer routers report per route name::

      {"status", "version", "database": {"status"},
       "routes": {"<route name>": "up|down"}}

``GET /v1/models`` lists the model ids; newer routers add
``x_router: {"route", "type"}`` to each entry, which is what ties an id to
its route.

A page must never wait on a slow router. ``snapshot()`` answers from memory
when it can: a snapshot younger than ``FRESH`` is returned as is, an older
one is returned while a refresh runs in the background, and only a missing
or very old one makes the caller wait, bounded by the HTTP timeout.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("mo_admin.router")

FRESH = 10.0
USABLE = 90.0
TIMEOUT = httpx.Timeout(4.0, connect=2.0)

_BACKEND_STATES = {"healthy", "unhealthy", "unavailable", "skipped"}


@dataclass(frozen=True)
class ModelEntry:
    id: str
    route: str | None = None
    type: str | None = None
    owned_by: str | None = None


@dataclass
class RouterStatus:
    configured: bool = True
    reachable: bool = False
    error: str | None = None
    status: str | None = None          # the router's own word: healthy, degraded, ok ...
    version: str | None = None
    database: str | None = None
    shape: str | None = None           # "routes" | "backends"
    routes: dict[str, str] = field(default_factory=dict)      # route name -> up | down
    backends: dict[str, str] = field(default_factory=dict)    # backend url -> state
    models: list[ModelEntry] = field(default_factory=list)
    models_error: str | None = None
    fetched_at: datetime | None = None

    # -- derived ------------------------------------------------------------
    @property
    def level(self) -> str:
        """One word for the lamp: ok | warn | down | off."""
        if not self.configured:
            return "off"
        if not self.reachable:
            return "down"
        if self.database_ok is False or self.routes_down or self.backends_down:
            return "warn"
        if self.status and self.status.lower() not in ("healthy", "ok", "up"):
            return "warn"
        return "ok"

    @property
    def database_ok(self) -> bool | None:
        if self.database is None:
            return None
        return self.database.lower() in ("healthy", "ok", "up")

    @property
    def routes_down(self) -> list[str]:
        return sorted(name for name, state in self.routes.items() if state == "down")

    @property
    def routes_up(self) -> int:
        return sum(1 for state in self.routes.values() if state == "up")

    @property
    def backends_down(self) -> list[str]:
        return sorted(url for url, state in self.backends.items() if state in ("unhealthy", "unavailable"))

    def route_state(self, name: str) -> str:
        """up | down | unknown for one route."""
        if not self.reachable:
            return "unknown"
        return self.routes.get(name, "unknown")

    def models_by_route(self) -> dict[str | None, list[ModelEntry]]:
        grouped: dict[str | None, list[ModelEntry]] = {}
        for model in self.models:
            grouped.setdefault(model.route, []).append(model)
        return grouped

    @property
    def summary(self) -> str:
        if not self.configured:
            return "Router status is not configured"
        if not self.reachable:
            return "Router unreachable"
        if self.shape == "routes" and self.routes:
            text = f"{self.routes_up} of {len(self.routes)} routes up"
        elif self.shape == "backends" and self.backends:
            probed = [s for s in self.backends.values() if s != "skipped"]
            healthy = sum(1 for s in probed if s == "healthy")
            text = f"{healthy} of {len(probed)} backends healthy"
        else:
            text = "Router up"
        if self.database_ok is False:
            text += ", database unavailable"
        return text


def parse_health(payload: Any) -> RouterStatus:
    """Normalise either health shape. Unknown fields are ignored."""
    status = RouterStatus(reachable=True)
    if not isinstance(payload, dict):
        status.error = "The router's /health answer was not a JSON object."
        return status
    if isinstance(payload.get("status"), str):
        status.status = payload["status"]
    if isinstance(payload.get("version"), str):
        status.version = payload["version"]
    database = payload.get("database")
    if isinstance(database, dict) and isinstance(database.get("status"), str):
        status.database = database["status"]
    elif isinstance(database, str):
        status.database = database

    routes = payload.get("routes")
    backends = payload.get("backends")
    if isinstance(routes, dict):
        status.shape = "routes"
        for name, state in routes.items():
            word = str(state).lower()
            status.routes[str(name)] = "up" if word in ("up", "healthy", "ok") else "down"
    elif isinstance(backends, dict):
        status.shape = "backends"
        for url, state in backends.items():
            word = str(state).lower()
            status.backends[str(url)] = word if word in _BACKEND_STATES else "unavailable"
    return status


def parse_models(payload: Any) -> list[ModelEntry]:
    entries: list[ModelEntry] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return entries
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        extra = item.get("x_router") if isinstance(item.get("x_router"), dict) else {}
        entries.append(
            ModelEntry(
                id=item["id"],
                route=extra.get("route") if isinstance(extra.get("route"), str) else None,
                type=extra.get("type") if isinstance(extra.get("type"), str) else None,
                owned_by=item.get("owned_by") if isinstance(item.get("owned_by"), str) else None,
            )
        )
    entries.sort(key=lambda m: m.id.lower())
    return entries


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "connection refused or host not found"
    if isinstance(exc, httpx.TimeoutException):
        return "no answer within the time limit"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, ValueError):
        return "the answer was not valid JSON"
    return type(exc).__name__


class RouterClient:
    def __init__(self, base_url: str | None, http: httpx.AsyncClient):
        self.base_url = base_url
        self._http = http
        self._snapshot: RouterStatus | None = None
        self._taken = 0.0
        self._refresh: asyncio.Task[RouterStatus] | None = None

    async def snapshot(self) -> RouterStatus:
        if not self.base_url:
            return RouterStatus(configured=False)
        age = time.monotonic() - self._taken
        if self._snapshot is not None and age < FRESH:
            return self._snapshot
        task = self._ensure_refresh()
        if self._snapshot is not None and age < USABLE:
            return self._snapshot
        return await asyncio.shield(task)

    def _ensure_refresh(self) -> asyncio.Task[RouterStatus]:
        if self._refresh is None or self._refresh.done():
            self._refresh = asyncio.create_task(self._fetch())
        return self._refresh

    async def aclose(self) -> None:
        if self._refresh is not None and not self._refresh.done():
            self._refresh.cancel()
            try:
                await self._refresh
            except (asyncio.CancelledError, Exception):
                pass

    async def _get_json(self, path: str) -> Any:
        response = await self._http.get(f"{self.base_url}{path}", timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()

    async def _fetch(self) -> RouterStatus:
        health, models = await asyncio.gather(
            self._get_json("/health"), self._get_json("/v1/models"), return_exceptions=True
        )
        if isinstance(health, Exception):
            status = RouterStatus(reachable=False, error=_describe(health))
            if not isinstance(health, (httpx.HTTPError, ValueError)):
                logger.warning("router health check failed unexpectedly: %r", health)
        else:
            status = parse_health(health)
            if isinstance(models, Exception):
                status.models_error = _describe(models)
            else:
                status.models = parse_models(models)
        status.fetched_at = datetime.now(timezone.utc)
        self._snapshot = status
        self._taken = time.monotonic()
        return status
