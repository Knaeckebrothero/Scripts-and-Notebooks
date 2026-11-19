"""The one object that holds the router's runtime state.

It is created in the application lifespan, wires the parts together, runs the
background loops (key refresh, probes, discovery, config watch, usage writer)
and is what handlers reach through ``request.app.state.router``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from collections.abc import Coroutine, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

from starlette.types import Message

from . import usage
from .auth import KeyCache
from .clients import ClientPool, TransportFactory
from .config import ConfigError, RouterConfig, config_warnings, load_config
from .context import RequestRecord
from .db import Database
from .discovery import Discoverer
from .errors import SOURCE_HEADER
from .health import DOWN, UP, HealthMonitor, HealthProber
from .limits import ConcurrencyLimiter, RateLimiter
from .logs import log_request, request_id_var
from .metrics import Metrics
from .proxy import Forwarder, ForwardPlan
from .registry import UPSTREAM, ModelEntry, Registry, Target, targets_from_config
from .settings import Settings

logger = logging.getLogger(__name__)

VOICES_PATH = "/v1/audio/voices"
VOICES_READ_TIMEOUT_S = 30.0
VOICES_TTL_S = 300.0
# A failed fetch is remembered briefly, so a backend that is failing is not
# asked again for every caller of this open endpoint.
VOICES_FAILURE_TTL_S = 5.0
_HOUSEKEEPING_INTERVAL_S = 30.0
_ROUTE_SYNC_RETRY_S = 30.0
# At shutdown: how long stop() waits for requests that are being ended to be recorded.
_STOP_WAIT_S = 5.0
# A cancelled request asks whether the server is shutting down. The server
# cancels requests a moment before it tells the application; this covers the gap.
_SHUTDOWN_NOTICE_S = 0.25

ConfigSignature = tuple[int, int, int] | None


def config_signature(path: str) -> ConfigSignature:
    """Changes whenever the file is edited or replaced (also by a ConfigMap symlink swap)."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


class StableChange:
    """Reports a changed file only once it has stopped changing.

    A file that is being written looks different on every poll. Acting on the
    first difference could load half a file, so a change counts when the
    signature (modification time, size) is the same on two polls in a row.
    """

    _NOTHING: Any = object()

    def __init__(self) -> None:
        self._candidate: Any = self._NOTHING

    def observe(self, current: ConfigSignature, applied: ConfigSignature) -> bool:
        """``current``: the file now. ``applied``: the file as last loaded. True: reload now."""
        if current == applied:
            self._candidate = self._NOTHING
            return False
        if current == self._candidate:
            self._candidate = self._NOTHING
            return True
        self._candidate = current
        return False


VoicesKey = tuple[str, str, str]


@dataclass(frozen=True)
class VoicesResult:
    """What a voices fetch gave: the answer every caller gets until it expires."""

    status: int
    body: bytes
    headers: dict[str, str]
    outcome: str | None
    upstream_status: int | None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.outcome == usage.OK


class VoicesCache:
    """Voice lists per route: a good answer for five minutes, a failure for a few seconds."""

    def __init__(self, ttl_s: float = VOICES_TTL_S, failure_ttl_s: float = VOICES_FAILURE_TTL_S) -> None:
        self.ttl_s = ttl_s
        self.failure_ttl_s = failure_ttl_s
        self._items: dict[VoicesKey, tuple[float, VoicesResult]] = {}

    def get(self, key: VoicesKey) -> VoicesResult | None:
        item = self._items.get(key)
        if item is None or item[0] <= time.monotonic():
            return None
        return item[1]

    def put(self, key: VoicesKey, result: VoicesResult) -> None:
        ttl = self.ttl_s if result.ok else self.failure_ttl_s
        if ttl > 0:
            self._items[key] = (time.monotonic() + ttl, result)

    def clear(self) -> None:
        self._items.clear()

    def prune(self) -> None:
        now = time.monotonic()
        self._items = {key: item for key, item in self._items.items() if item[0] > now}


async def _never_disconnects() -> Message:
    """``receive`` for a fetch that belongs to no single client."""
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


class RouterState:
    def __init__(
        self,
        settings: Settings,
        config: RouterConfig,
        *,
        database: Database | None = None,
        transport_factory: TransportFactory | None = None,
        listen_host: str = "0.0.0.0",
    ) -> None:
        if database is None:
            if not settings.database_url:
                raise RuntimeError("ROUTER_DATABASE_URL is not set")
            database = Database(settings.database_url, migrations_dir=settings.migrations_dir)
        self.settings = settings
        self.config = config
        self.listen_host = listen_host
        self.started_at = time.time()
        self.config_loaded_at = time.time()
        self.last_reload_error: str | None = None
        self.metrics = Metrics(version=settings.version, git_sha=settings.git_sha)
        self.clients = ClientPool(transport_factory, max_connections=settings.upstream_max_connections)
        targets = targets_from_config(config, os.environ, discovery_interval=settings.discovery_interval)
        self.registry = Registry(targets)
        self.health = HealthMonitor(
            fails=settings.health_fails,
            unhealthy_seconds=settings.unhealthy_seconds,
            gating=settings.health_gating,
        )
        self.health.configure(targets)
        self.prober = HealthProber(
            self.health,
            self.clients,
            interval_s=settings.health_interval,
            timeout_s=settings.health_timeout,
            metrics=self.metrics,
            on_recovered=self._on_recovered,
        )
        self.discoverer = Discoverer(
            self.registry, self.health, self.clients, on_change=self._on_table_change
        )
        self.keys = KeyCache()
        self.rate_limiter = RateLimiter()
        self.concurrency = ConcurrencyLimiter()
        self.db = database
        self.usage = usage.UsageWriter(self.db.write_usage, metrics=self.metrics)
        self.forwarder = Forwarder(
            self.clients,
            self.health,
            self.metrics,
            self.prober,
            client_write_timeout_s=settings.client_write_timeout_s,
            shutting_down=self.wait_shutting_down,
        )
        self.voices = VoicesCache()
        self._voices_fetches: dict[VoicesKey, asyncio.Task[VoicesResult]] = {}
        # Set when the process was told to stop (by the server's signal handler)
        # or, at the latest, when stop() begins.
        self.shutting_down = False
        self._shutdown_started = asyncio.Event()
        self._active_requests = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._tasks: list[asyncio.Task[None]] = []
        self._usage_task: asyncio.Task[None] | None = None
        self._one_shots: set[asyncio.Task[Any]] = set()
        self._reload_lock = asyncio.Lock()
        self._config_signature: ConfigSignature = None
        self._synced_routes: dict[str, str] | None = None
        self._route_sync_wanted = asyncio.Event()
        self._sighup_installed = False
        self._sighup_pending = False
        self._ready = False
        self._internal: Any = None
        self.metrics.add_state_source(self._state_samples)

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        try:
            await self._start()
        except BaseException:
            # A failed start must not leave tasks or connections behind.
            with contextlib.suppress(Exception):
                await self.stop()
            raise

    async def _start(self) -> None:
        settings = self.settings
        # First thing: without a handler a SIGHUP would end the process.
        self._install_sighup()
        self._config_signature = config_signature(settings.config_path)
        self.clients.ensure(self.registry.targets)
        logger.info(
            "starting: version %s (%s), %d outbound client(s)",
            settings.version,
            settings.git_sha,
            len(self.clients),
        )

        await self.db.open()
        await self.db.migrate()
        await self._sync_routes()
        self.keys.replace(await self.db.load_keys())
        if len(self.keys) == 0:
            logger.warning(
                "key store: the api_keys table is empty; every request is rejected until a key is created"
            )
        logger.info(
            "key store: %d key(s) loaded, refresh every %.0f s", len(self.keys), settings.keys_refresh_seconds
        )

        # One probe round before the listener opens; its result is taken at face value.
        await self.prober.probe_all()
        await self.discoverer.discover(self.registry.targets)
        table = self.registry.table
        logger.info(
            "ready: %d route(s), %d upstream(s), %d model id(s), health gating %s",
            sum(1 for t in table.targets if not t.is_upstream),
            sum(1 for t in table.targets if t.is_upstream),
            len(table.entries),
            "on" if settings.health_gating else "off",
        )

        self._usage_task = asyncio.create_task(self.usage.run(), name="usage-writer")
        loops: list[tuple[str, Coroutine[Any, Any, None]]] = [
            ("key-refresh", self._key_refresh_loop()),
            ("health-probes", self.prober.run()),
            ("discovery", self.discoverer.run()),
            ("route-sync", self._route_sync_loop()),
            ("housekeeping", self._housekeeping_loop()),
        ]
        if settings.config_poll_seconds > 0:
            loops.append(("config-watch", self._config_watch_loop()))
        self._tasks = [asyncio.create_task(coro, name=name) for name, coro in loops]

        if settings.metrics_port > 0:
            from .internal import InternalServer

            self._internal = InternalServer(
                self, settings.metrics_host or self.listen_host, settings.metrics_port
            )
            await self._internal.start()

        self._ready = True
        if self._sighup_pending:
            self._sighup_pending = False
            self._spawn(self.reload("SIGHUP during startup"))

    async def stop(self) -> None:
        self.shutting_down = True
        self._shutdown_started.set()
        self._ready = False
        self._remove_sighup()
        # Requests the server is ending right now still have to answer their
        # clients and be recorded. Bounded: shutdown must not hang on them.
        if self._active_requests:
            try:
                await asyncio.wait_for(self._idle.wait(), timeout=_STOP_WAIT_S)
            except TimeoutError:
                logger.warning(
                    "shutdown: %d request(s) did not end in %.0f s and are not recorded",
                    self._active_requests,
                    _STOP_WAIT_S,
                )
        if self._internal is not None:
            await self._internal.stop()
        fetches = list(self._voices_fetches.values())
        for task in [*self._tasks, *self._one_shots, *fetches]:
            task.cancel()
        await asyncio.gather(*self._tasks, *self._one_shots, *fetches, return_exceptions=True)
        self._tasks = []
        await self.usage.stop(self._usage_task)
        self._usage_task = None
        await self.db.close()
        await self.clients.aclose()
        logger.info("shutdown complete")

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._one_shots.add(task)
        task.add_done_callback(self._one_shots.discard)

    def _install_sighup(self) -> None:
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError, AttributeError):
            asyncio.get_running_loop().add_signal_handler(signal.SIGHUP, self._on_sighup)
            self._sighup_installed = True

    def _on_sighup(self) -> None:
        if self._ready:
            self._spawn(self.reload("SIGHUP"))
        else:
            # Still starting: remember it and reload once the router is up.
            self._sighup_pending = True

    def _remove_sighup(self) -> None:
        if self._sighup_installed:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                asyncio.get_running_loop().remove_signal_handler(signal.SIGHUP)
            self._sighup_installed = False

    # -- shutdown ---------------------------------------------------------------

    def note_shutdown(self) -> None:
        """The process was told to stop. Only sets a flag: safe to call from a signal handler."""
        self.shutting_down = True

    async def wait_shutting_down(self) -> bool:
        """Asked by a request that was cancelled: is the server ending requests?

        A request is only ever cancelled by the server, when its shutdown grace
        period is over. The answer decides between ending the request properly
        (an error event, a 503) and letting the cancellation through.
        """
        if not self.shutting_down:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown_started.wait(), timeout=_SHUTDOWN_NOTICE_S)
        return self.shutting_down

    def request_started(self) -> None:
        self._active_requests += 1
        self._idle.clear()

    def request_finished(self) -> None:
        self._active_requests = max(0, self._active_requests - 1)
        if self._active_requests == 0:
            self._idle.set()

    # -- what requests ask ------------------------------------------------------

    def is_available(self, entry: ModelEntry) -> bool:
        """A known id is available when its route has a backend that is not down
        and, for an id learned from a backend, when that backend still lists it."""
        return entry.listed and self.health.available(entry.target)

    def finish(self, record: RequestRecord) -> None:
        """A request is over: metrics, the usage event and the log line."""
        key = record.key
        route = record.route
        if record.forwarded and route:
            self.metrics.duration.labels(route).observe(record.duration_s())
            ttfb = record.ttfb_s()
            if ttfb is not None:
                self.metrics.ttfb.labels(route).observe(ttfb)
        if record.outcome in usage.OUTCOMES and key is not None and record.model and route:
            labels = (route, record.model, str(key.id), key.name)
            self.metrics.requests.labels(*labels, record.outcome).inc()
            if record.prompt_tokens:
                self.metrics.tokens.labels(*labels, "prompt").inc(record.prompt_tokens)
            if record.completion_tokens:
                self.metrics.tokens.labels(*labels, "completion").inc(record.completion_tokens)
            self.usage.record(
                usage.UsageEvent(
                    at=datetime.now(UTC),
                    key_id=key.id,
                    owner=key.owner,
                    model=record.model,
                    route=route,
                    outcome=record.outcome,
                    prompt_tokens=record.prompt_tokens or 0,
                    completion_tokens=record.completion_tokens or 0,
                    duration_ms=round(record.duration_s() * 1000),
                )
            )
        if record.reason:
            self.metrics.rejections.labels(record.reason).inc()
        quiet = record.quiet and (record.status or 0) < 400
        log_request(record.log_fields(), level=logging.DEBUG if quiet else logging.INFO)

    # -- health views -------------------------------------------------------------

    def route_states(self) -> dict[str, str]:
        """Up or down per route name, from cached probe state."""
        states: dict[str, bool] = {}
        table = self.registry.table
        for target in table.targets:
            if not target.is_upstream:
                states[target.name] = self.health.target_up(target)
        for entry in table.entries.values():
            if entry.source == UPSTREAM:
                up = entry.listed and self.health.target_up(entry.target)
                states[entry.route_name] = states.get(entry.route_name, False) or up
        return {name: (UP if up else DOWN) for name, up in states.items()}

    def public_health(self) -> dict[str, Any]:
        healthy = self.db.healthy
        return {
            "status": "healthy" if healthy else "degraded",
            "router": "model-orchestrator",
            "version": self.settings.version,
            "database": {"status": "healthy" if healthy else "unavailable"},
            "routes": self.route_states(),
        }

    async def detailed_health(self) -> dict[str, Any]:
        """The operator's view (internal port only): backend URLs, key store, discovery."""
        await self.db.ping()
        table = self.registry.table
        now = time.time()

        def age(stamp: float | None) -> float | None:
            return None if stamp is None else round(now - stamp, 1)

        targets: dict[str, list[dict[str, Any]]] = {"routes": [], "upstreams": []}
        for target in table.targets:
            backends = []
            for backend in target.backends:
                key = self.health.probe_key(target, backend)
                state = self.health.state(key) if key is not None else None
                backends.append(
                    {
                        "url": backend,
                        "status": self.health.status(target, backend),
                        "consecutive_failures": state.consecutive_failures if state else 0,
                        "seconds_since_probe": age(state.last_probe_at) if state else None,
                        "last_error": state.last_error if state else None,
                        "passive_mark_seconds": round(self.health.passive_remaining(backend), 1),
                    }
                )
            discovery = self.registry.discovery_state(target)
            item: dict[str, Any] = {
                "name": target.name,
                "status": UP if self.health.target_up(target) else DOWN,
                "health_path": target.health_path,
                "backends": backends,
                "discovery": {
                    "known_ids": sorted(discovery.known),
                    "listed_ids": sorted(discovery.listed),
                    "seconds_since_success": age(discovery.last_success),
                    "last_error": discovery.last_error,
                },
            }
            if target.is_upstream:
                targets["upstreams"].append(item)
            else:
                item["type"] = target.route_type
                item["aliases"] = list(target.aliases)
                targets["routes"].append(item)
        key_age = self.keys.age_seconds()
        return {
            "status": "healthy" if self.db.healthy else "degraded",
            "router": "model-orchestrator",
            "version": self.settings.version,
            "git_sha": self.settings.git_sha,
            "uptime_seconds": round(now - self.started_at, 1),
            "health_gating": self.settings.health_gating,
            "database": {
                "status": "healthy" if self.db.healthy else "unavailable",
                "error": self.db.last_error,
                "keys_loaded": len(self.keys),
                "seconds_since_key_refresh": None if key_age is None else round(key_age, 1),
                "usage_queue_depth": self.usage.depth(),
                "usage_records_dropped": self.usage.dropped,
            },
            "config": {
                "path": self.settings.config_path,
                "seconds_since_load": age(self.config_loaded_at),
                "last_reload_error": self.last_reload_error,
            },
            "models": {
                "known": len(table.entries),
                "available": sum(1 for entry in table.entries.values() if self.is_available(entry)),
            },
            **targets,
        }

    def _state_samples(self) -> Iterable[tuple[str, str, list[str], list[tuple[list[str], float]]]]:
        table = self.registry.table
        backend_up: list[tuple[list[str], float]] = []
        route_up: list[tuple[list[str], float]] = []
        for target in table.targets:
            route_up.append(([target.name, target.kind], 1.0 if self.health.target_up(target) else 0.0))
            for backend in target.backends:
                status = self.health.status(target, backend)
                if status in (UP, DOWN):
                    backend_up.append(([target.name, backend], 1.0 if status == UP else 0.0))
        key_age = self.keys.age_seconds()
        yield (
            "router_backend_up",
            "Active probe state per backend (1 up, 0 down)",
            ["route", "backend"],
            backend_up,
        )
        yield (
            "router_route_up",
            "1 while a route or upstream has a backend that is not down",
            ["route", "kind"],
            route_up,
        )
        yield "router_keys_loaded", "API keys in the cache", [], [([], float(len(self.keys)))]
        yield (
            "router_key_cache_age_seconds",
            "Seconds since the key cache was last refreshed from the database",
            [],
            [([], float(key_age))] if key_age is not None else [],
        )
        yield (
            "router_database_up",
            "1 while the last database operation succeeded",
            [],
            [([], float(self.db.healthy))],
        )
        yield (
            "router_usage_queue_depth",
            "Usage records waiting to be written",
            [],
            [([], float(self.usage.depth()))],
        )
        yield (
            "router_models",
            "Model ids by state",
            ["state"],
            [
                (["known"], float(len(table.entries))),
                (["available"], float(sum(1 for e in table.entries.values() if self.is_available(e)))),
            ],
        )
        yield (
            "router_config_loaded_timestamp_seconds",
            "When the running config was loaded",
            [],
            [([], self.config_loaded_at)],
        )
        yield (
            "router_key_requests_in_flight",
            "Requests currently admitted, all keys",
            [],
            [([], float(self.concurrency.total()))],
        )

    # -- config reload --------------------------------------------------------------

    async def reload(self, reason: str) -> bool:
        """Re-read the config file. An invalid file leaves the running config in place."""
        async with self._reload_lock:
            path = self.settings.config_path
            self._config_signature = config_signature(path)
            try:
                config = load_config(path)
                if not config.routes and not config.upstreams:
                    # Valid at startup, refused here: a truncated or half-written file
                    # must not wipe the route table of a running router.
                    raise ConfigError(
                        [
                            "the file defines no routes and no upstreams; a reload never empties "
                            "the route table (restart the router if that is what you want)"
                        ]
                    )
                targets = targets_from_config(
                    config, os.environ, discovery_interval=self.settings.discovery_interval
                )
                self.clients.ensure(targets)
            except Exception as exc:
                messages = exc.messages if isinstance(exc, ConfigError) else [f"{type(exc).__name__}: {exc}"]
                self.last_reload_error = "; ".join(messages)
                self.metrics.config_reloads.labels("error").inc()
                logger.error("config reload (%s) rejected, keeping the running config:", reason)
                for message in messages:
                    logger.error("  config: %s", message)
                return False
            # No await between these three: requests see the old or the new config, never a mix.
            previous = self.config
            self.health.configure(targets)
            self.registry.replace_targets(targets)
            self.config = config
            self.voices.clear()
            self.config_loaded_at = time.time()
            self.last_reload_error = None
            self.metrics.config_reloads.labels("ok").inc()
            logger.info(
                "config reloaded (%s): %d route(s), %d upstream(s)",
                reason,
                len(config.routes),
                len(config.upstreams),
            )
            for warning in config_warnings(config, os.environ):
                logger.warning("config: %s", warning)
            if config.server != previous.server:
                logger.warning("config: the server block changed, but it only applies at start")
            await self.prober.probe_unknown()
            await self.discoverer.discover(self.registry.targets)
            self._route_sync_wanted.set()
            return True

    async def _config_watch_loop(self) -> None:
        change = StableChange()
        while True:
            await asyncio.sleep(self.settings.config_poll_seconds)
            try:
                if change.observe(config_signature(self.settings.config_path), self._config_signature):
                    await self.reload("file changed")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("config watch failed")

    # -- background work ---------------------------------------------------------------

    def _on_recovered(self, targets: list[Target]) -> None:
        self.discoverer.trigger(targets)

    def _on_table_change(self) -> None:
        self.voices.clear()
        self._route_sync_wanted.set()

    # -- voices ---------------------------------------------------------------------------

    async def fetch_voices(self, entry: ModelEntry, key: VoicesKey) -> VoicesResult:
        """The voice list of a route, asking the backend at most once at a time.

        Callers that arrive while a fetch is under way wait for that fetch. The
        endpoint needs no key, so without this every caller would open a backend
        connection of its own.
        """
        task = self._voices_fetches.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch_voices(entry, key), name="voices-fetch")
            self._voices_fetches[key] = task
            task.add_done_callback(partial(self._voices_fetch_done, key))
        # Shielded: one caller going away must not take the answer from the others.
        return await asyncio.shield(task)

    def _voices_fetch_done(self, key: VoicesKey, task: asyncio.Task[VoicesResult]) -> None:
        if self._voices_fetches.get(key) is task:
            del self._voices_fetches[key]
        if not task.cancelled():
            task.exception()  # looked at here, so a fetch nobody waited for is not reported as lost

    async def _fetch_voices(self, entry: ModelEntry, key: VoicesKey) -> VoicesResult:
        target = entry.target
        record = RequestRecord(
            request_id=request_id_var.get(),
            method="GET",
            path=VOICES_PATH,
            model=entry.model_id,
            route=entry.route_name,
        )
        plan = ForwardPlan(
            entry=entry,
            method="GET",
            path=VOICES_PATH,
            read_timeout_s=VOICES_READ_TIMEOUT_S,
            params={"model": entry.model_id} if target.is_upstream else None,
            count_tokens=False,
        )
        try:
            response = await self.forwarder.forward(plan, record, _never_disconnects)
            kept = ("content-type", SOURCE_HEADER.lower(), "retry-after")
            result = VoicesResult(
                status=response.status_code,
                body=bytes(response.body),
                headers={name: response.headers[name] for name in kept if name in response.headers},
                outcome=record.outcome,
                upstream_status=record.upstream_status,
            )
        finally:
            await record.run_cleanups()
        self.metrics.duration.labels(entry.route_name).observe(record.duration_s())
        self.voices.put(key, result)
        if not result.ok:
            logger.info(
                "voices of route '%s': %s (HTTP %d), remembered for %.0f s",
                entry.route_name,
                result.outcome,
                result.status,
                self.voices.failure_ttl_s,
            )
        return result

    async def _sync_routes(self) -> None:
        table = self.registry.table
        names = table.route_names()
        # With an upstream router configured, rows are only added: its route names
        # are not all known while it is unreachable or one of its routes is down.
        prune = not any(target.is_upstream for target in table.targets)
        await self.db.sync_routes(names, prune=prune)
        self._synced_routes = names

    async def _route_sync_loop(self) -> None:
        while True:
            await self._route_sync_wanted.wait()
            self._route_sync_wanted.clear()
            if self.registry.table.route_names() == self._synced_routes:
                continue
            try:
                await self._sync_routes()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("route sync failed, will retry: %s", type(exc).__name__)
                await asyncio.sleep(_ROUTE_SYNC_RETRY_S)
                self._route_sync_wanted.set()

    async def _key_refresh_loop(self) -> None:
        failing = False
        while True:
            await asyncio.sleep(self.settings.keys_refresh_seconds)
            try:
                keys = await self.db.load_keys()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.metrics.key_refresh_failures.inc()
                if not failing:
                    logger.warning(
                        "key store: refresh failed, keeping the last known keys: %s: %s",
                        type(exc).__name__,
                        " ".join(str(exc).split())[:200],
                    )
                failing = True
                continue
            if failing:
                logger.info("key store: refresh works again")
            failing = False
            if len(keys) != len(self.keys):
                logger.info("key store: key set changed, %d key(s) active", len(keys))
            self.keys.replace(keys)

    async def _housekeeping_loop(self) -> None:
        while True:
            await asyncio.sleep(_HOUSEKEEPING_INTERVAL_S)
            self.rate_limiter.sweep()
            self.health.prune_passive()
            self.voices.prune()
