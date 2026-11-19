"""The model table: which model id goes where.

A *target* is something requests are forwarded to: a local route (one backend or
a pool) or an upstream router. The table maps every routable model id to a
target and is rebuilt as an immutable snapshot whenever the config or a
discovery result changes, so a request always works with one consistent view.

Precedence, as before: a configured alias beats a discovered id; among
discovered ids the first route in config order wins; local routes beat upstream
ids; between upstreams the first in config order wins.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any

from .config import ROUTE_TYPES, RouterConfig

ROUTE = "route"
UPSTREAM = "upstream"

# Type stored in the routes table for an upstream that does not report types.
UNKNOWN_ROUTE_TYPE = "upstream"

_STUB_CREATED = 1704067200


@dataclass(frozen=True)
class ClientKey:
    """What distinguishes one outbound HTTP client from another."""

    proxy: str | None = None
    verify: bool = True
    ca_file: str | None = None


@dataclass(frozen=True, eq=False)
class Target:
    kind: str
    name: str
    backends: tuple[str, ...]
    client_key: ClientKey
    api_key: str | None
    health_path: str | None
    discovery_interval: float
    # Local routes only.
    route_type: str | None = None
    endpoint: str | None = None
    backend_model_name: str | None = None
    aliases: tuple[str, ...] = ()
    owned_by: str | None = None
    request_defaults: Mapping[str, Any] = field(default_factory=dict)
    timeout_s: float | None = None
    usage_from_stream: bool = True
    # Upstream routers only.
    include: tuple[str, ...] = ("*",)
    exclude: tuple[str, ...] = ()

    @property
    def ident(self) -> tuple[str, str]:
        return (self.kind, self.name)

    @property
    def is_upstream(self) -> bool:
        return self.kind == UPSTREAM

    def admits(self, model_id: str) -> bool:
        """Upstream id filter: matches an include glob and no exclude glob."""
        if not any(fnmatchcase(model_id, pattern) for pattern in self.include):
            return False
        return not any(fnmatchcase(model_id, pattern) for pattern in self.exclude)


@dataclass(frozen=True, eq=False)
class ModelEntry:
    model_id: str
    target: Target
    # The name used for allowlists and usage. For an upstream id it comes from
    # the upstream's x_router object, so numbers stay comparable across sites.
    route_name: str
    # None for an upstream that does not report types: no type check here.
    route_type: str | None
    # Set for aliases: the model name sent to the backend. None: forwarded as sent.
    rewrite_to: str | None
    meta: Mapping[str, Any] | None
    source: str  # "alias" | "discovered" | "upstream"
    # False once a backend that answered no longer lists the id. Aliases are always listed.
    listed: bool = True


@dataclass(frozen=True)
class ModelTable:
    entries: Mapping[str, ModelEntry]
    targets: tuple[Target, ...]

    def get(self, model_id: object) -> ModelEntry | None:
        if not isinstance(model_id, str) or not model_id:
            return None
        return self.entries.get(model_id)

    def first_of_type(self, route_type: str) -> ModelEntry | None:
        for entry in self.entries.values():
            if entry.route_type == route_type:
                return entry
        return None

    def route_names(self) -> dict[str, str]:
        """Route name to type: every local route, plus the routes upstream ids belong to."""
        names: dict[str, str] = {}
        for target in self.targets:
            if not target.is_upstream:
                names[target.name] = target.route_type or UNKNOWN_ROUTE_TYPE
        for entry in self.entries.values():
            if entry.source == UPSTREAM:
                names.setdefault(entry.route_name, entry.route_type or UNKNOWN_ROUTE_TYPE)
        return names


@dataclass
class DiscoveryState:
    """What a target's ``/v1/models`` has told us so far."""

    # Every id ever listed, with its latest entry. Remembered for as long as the
    # target stays in the config, so an id whose backend goes down is still known.
    known: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The ids of the most recent listing.
    listed: frozenset[str] = frozenset()
    last_success: float | None = None
    last_attempt: float | None = None
    last_error: str | None = None
    failures: int = 0


def targets_from_config(
    config: RouterConfig, env: Mapping[str, str], *, discovery_interval: float
) -> list[Target]:
    targets: list[Target] = []
    for route in config.routes:
        targets.append(
            Target(
                kind=ROUTE,
                name=route.name,
                backends=tuple(route.backend),
                client_key=ClientKey(proxy=route.proxy),
                api_key=(env.get(route.backend_api_key_env) or None) if route.backend_api_key_env else None,
                health_path=route.health_path,
                discovery_interval=discovery_interval,
                route_type=route.type,
                endpoint=route.endpoint,
                backend_model_name=route.backend_model_name,
                aliases=tuple(dict.fromkeys(route.models)),
                owned_by=route.owned_by,
                request_defaults=route.request_defaults,
                timeout_s=route.timeout_s,
                usage_from_stream=route.usage_from_stream,
            )
        )
    for upstream in config.upstreams:
        targets.append(
            Target(
                kind=UPSTREAM,
                name=upstream.name,
                backends=(upstream.base_url,),
                client_key=ClientKey(
                    proxy=upstream.proxy, verify=upstream.tls_verify, ca_file=upstream.ca_file
                ),
                api_key=(env.get(upstream.api_key_env) or None) if upstream.api_key_env else None,
                health_path=upstream.health_path,
                discovery_interval=upstream.discovery_interval,
                include=tuple(upstream.include),
                exclude=tuple(upstream.exclude),
            )
        )
    return targets


def build_table(
    targets: Sequence[Target], discovered: Mapping[tuple[str, str], DiscoveryState]
) -> ModelTable:
    entries: dict[str, ModelEntry] = {}
    for target in targets:
        if target.is_upstream:
            continue
        state = discovered.get(target.ident)
        known = state.known if state else {}
        listed = state.listed if state else frozenset()
        for alias in target.aliases:
            effective = target.backend_model_name or alias
            entries[alias] = ModelEntry(
                model_id=alias,
                target=target,
                route_name=target.name,
                route_type=target.route_type,
                rewrite_to=effective,
                meta=known.get(effective),
                source="alias",
            )
        for model_id, meta in known.items():
            if model_id in entries:
                continue
            entries[model_id] = ModelEntry(
                model_id=model_id,
                target=target,
                route_name=target.name,
                route_type=target.route_type,
                rewrite_to=None,
                meta=meta,
                source="discovered",
                listed=model_id in listed,
            )
    for target in targets:
        if not target.is_upstream:
            continue
        state = discovered.get(target.ident)
        if state is None:
            continue
        for model_id, meta in state.known.items():
            if model_id in entries or not target.admits(model_id):
                continue
            route_name, route_type = _upstream_route(meta, target)
            entries[model_id] = ModelEntry(
                model_id=model_id,
                target=target,
                route_name=route_name,
                route_type=route_type,
                rewrite_to=None,
                meta=meta,
                source=UPSTREAM,
                listed=model_id in state.listed,
            )
    return ModelTable(entries=entries, targets=tuple(targets))


def _upstream_route(meta: Mapping[str, Any], target: Target) -> tuple[str, str | None]:
    """Route name and type of an upstream id, from the upstream's ``x_router`` object.

    An older router sends none: the route name is then the upstream's name and
    there is no type, so no type check is made here (the upstream makes its own).
    """
    info = meta.get("x_router")
    if not isinstance(info, dict):
        return target.name, None
    route = info.get("route")
    route_type = info.get("type")
    return (
        route if isinstance(route, str) and route else target.name,
        route_type if isinstance(route_type, str) and route_type in ROUTE_TYPES else None,
    )


def model_object(entry: ModelEntry) -> dict[str, Any]:
    """The ``/v1/models`` entry for a model id.

    The backend's own entry passes through (``max_model_len``, ``permission``,
    ``created``, ...). The router sets ``id``, ``owned_by`` when the route
    configures one, and ``x_router``.
    """
    if entry.meta:
        obj = dict(entry.meta)
        obj["id"] = entry.model_id
        if entry.target.owned_by:
            obj["owned_by"] = entry.target.owned_by
    else:
        obj = {
            "id": entry.model_id,
            "object": "model",
            "created": _STUB_CREATED,
            "owned_by": entry.target.owned_by or "local",
            "permission": [],
            "root": entry.model_id,
            "parent": None,
        }
    obj["x_router"] = {"route": entry.route_name, "type": entry.route_type}
    return obj


class Registry:
    """Holds the targets, what discovery has learned, and the current table."""

    def __init__(self, targets: Sequence[Target]) -> None:
        self._targets: tuple[Target, ...] = tuple(targets)
        self._discovered: dict[tuple[str, str], DiscoveryState] = {t.ident: DiscoveryState() for t in targets}
        self._table = build_table(self._targets, self._discovered)

    @property
    def table(self) -> ModelTable:
        return self._table

    @property
    def targets(self) -> tuple[Target, ...]:
        return self._targets

    def is_current(self, target: Target) -> bool:
        return any(t is target for t in self._targets)

    def discovery_state(self, target: Target) -> DiscoveryState:
        return self._discovered.setdefault(target.ident, DiscoveryState())

    def replace_targets(self, targets: Sequence[Target]) -> None:
        """Swap in a new config. Discovered ids survive for targets that stay."""
        kept = {t.ident for t in targets}
        discovered = {ident: state for ident, state in self._discovered.items() if ident in kept}
        for target in targets:
            discovered.setdefault(target.ident, DiscoveryState())
        self._targets = tuple(targets)
        self._discovered = discovered
        self._table = build_table(self._targets, self._discovered)

    def apply_listing(self, target: Target, models: Sequence[Any]) -> bool:
        """Record a successful ``/v1/models`` answer.

        Returns True when routing changed: an id appeared or disappeared, or an
        ``x_router`` object (route name, type) is different. Other metadata is
        taken over silently; vLLM alone changes ``created`` and a permission id
        on every call, and that must not look like news.
        """
        if not self.is_current(target):
            return False
        state = self.discovery_state(target)
        found: dict[str, dict[str, Any]] = {}
        for item in models:
            if isinstance(item, dict):
                model_id = item.get("id")
                if isinstance(model_id, str) and model_id and target.admits(model_id):
                    found[model_id] = item
        now = time.time()
        state.last_attempt = now
        state.last_success = now
        state.last_error = None
        state.failures = 0
        listed = frozenset(found)
        merged = dict(state.known)
        merged.update(found)
        if merged == state.known and listed == state.listed:
            return False
        routing_changed = (
            listed != state.listed
            or merged.keys() != state.known.keys()
            or any(
                item.get("x_router") != state.known[model_id].get("x_router")
                for model_id, item in found.items()
                if model_id in state.known
            )
        )
        state.known = merged
        state.listed = listed
        self._table = build_table(self._targets, self._discovered)
        return routing_changed

    def note_no_listing(self, target: Target, error: str, *, answered: bool) -> None:
        """Discovery got no usable listing. What is known stays as it is."""
        if not self.is_current(target):
            return
        state = self.discovery_state(target)
        state.last_attempt = time.time()
        state.last_error = error
        state.failures = 0 if answered else state.failures + 1
