"""Outbound HTTP clients: two per distinct network path (proxy and TLS settings).

* The **traffic** client carries forwarded requests. Its connection limit is
  ``ROUTER_UPSTREAM_MAX_CONNECTIONS``. A request that cannot get a connection
  within ``POOL_TIMEOUT_S`` is answered ``503 router_busy``; it does not wait for
  the read timeout.
* The **control** client carries health probes and discovery. It has a pool of
  its own, so traffic can never starve it, however many requests are in flight.
  The pool is small, but sized from the config so that probes cannot starve
  each other either.

Clients are created when a config is loaded and closed at shutdown; one that a
reload no longer needs is kept, because a request in flight may still be using it.
"""

from __future__ import annotations

import ssl
from collections import Counter
from collections.abc import Callable, Iterable

import httpx

from .registry import ClientKey, Target

CONNECT_TIMEOUT_S = 10.0
# How long a request may wait for a free connection of the traffic pool.
POOL_TIMEOUT_S = 5.0
CONTROL_MIN_CONNECTIONS = 8

TransportFactory = Callable[[ClientKey], httpx.AsyncBaseTransport]


def request_timeout(read_timeout_s: float) -> httpx.Timeout:
    """Per-request timeout: the endpoint's read timeout, 10 s to connect, 5 s for a pooled connection."""
    return httpx.Timeout(read_timeout_s, connect=CONNECT_TIMEOUT_S, pool=POOL_TIMEOUT_S)


def control_pool_size(backends: int) -> int:
    """Room for a probe, an extra probe asked for by a failed request, and a discovery call per backend."""
    return max(CONTROL_MIN_CONNECTIONS, 3 * backends + 2)


class ClientPool:
    def __init__(
        self, transport_factory: TransportFactory | None = None, *, max_connections: int = 1000
    ) -> None:
        self._traffic: dict[ClientKey, httpx.AsyncClient] = {}
        self._control: dict[ClientKey, tuple[httpx.AsyncClient, int]] = {}
        self._retired: list[httpx.AsyncClient] = []
        self._transport_factory = transport_factory
        self._max_connections = max_connections

    def ensure(self, targets: Iterable[Target]) -> None:
        """Create the clients a config needs. Raises if one cannot be built (nothing is changed then)."""
        backends: Counter[ClientKey] = Counter()
        for target in targets:
            backends[target.client_key] += len(target.backends)
        traffic = {key: self._make_traffic(key) for key in backends if key not in self._traffic}
        control: dict[ClientKey, tuple[httpx.AsyncClient, int]] = {}
        for key, count in backends.items():
            size = control_pool_size(count)
            current = self._control.get(key)
            if current is None or current[1] < size:
                control[key] = (self._make_control(key, size), size)
        self._traffic.update(traffic)
        for key, entry in control.items():
            previous = self._control.get(key)
            if previous is not None:
                # A probe may be using it right now: closed at shutdown, not here.
                self._retired.append(previous[0])
            self._control[key] = entry

    def get(self, key: ClientKey) -> httpx.AsyncClient:
        """The client for forwarded requests."""
        client = self._traffic.get(key)
        if client is None:
            client = self._traffic[key] = self._make_traffic(key)
        return client

    def control(self, key: ClientKey) -> httpx.AsyncClient:
        """The client for probes and discovery. Never the one traffic uses."""
        entry = self._control.get(key)
        if entry is None:
            entry = self._control[key] = (
                self._make_control(key, CONTROL_MIN_CONNECTIONS),
                CONTROL_MIN_CONNECTIONS,
            )
        return entry[0]

    def __len__(self) -> int:
        return len(self._traffic)

    async def aclose(self) -> None:
        clients = [*self._traffic.values(), *(c for c, _ in self._control.values()), *self._retired]
        self._traffic, self._control, self._retired = {}, {}, []
        for client in clients:
            await client.aclose()

    def _make_traffic(self, key: ClientKey) -> httpx.AsyncClient:
        limits = httpx.Limits(
            max_connections=self._max_connections,
            max_keepalive_connections=min(100, self._max_connections),
        )
        return self._make(key, limits)

    def _make_control(self, key: ClientKey, size: int) -> httpx.AsyncClient:
        return self._make(key, httpx.Limits(max_connections=size, max_keepalive_connections=size))

    def _make(self, key: ClientKey, limits: httpx.Limits) -> httpx.AsyncClient:
        timeout = httpx.Timeout(120.0, connect=CONNECT_TIMEOUT_S, pool=POOL_TIMEOUT_S)
        if self._transport_factory is not None:
            return httpx.AsyncClient(transport=self._transport_factory(key), timeout=timeout)
        verify: ssl.SSLContext | bool = True
        if not key.verify:
            verify = False
        elif key.ca_file:
            verify = ssl.create_default_context(cafile=key.ca_file)
        return httpx.AsyncClient(timeout=timeout, limits=limits, http2=True, proxy=key.proxy, verify=verify)
