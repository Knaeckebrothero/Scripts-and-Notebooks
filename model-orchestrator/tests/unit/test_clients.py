"""Outbound clients: the control plane (probes, discovery) never shares a pool with traffic."""

from __future__ import annotations

import httpx
import pytest

from model_orchestrator.clients import (
    CONTROL_MIN_CONNECTIONS,
    POOL_TIMEOUT_S,
    ClientPool,
    control_pool_size,
    request_timeout,
)
from model_orchestrator.context import RequestRecord
from model_orchestrator.discovery import Discoverer
from model_orchestrator.health import UP, HealthMonitor, HealthProber
from model_orchestrator.metrics import Metrics
from model_orchestrator.proxy import Forwarder, ForwardPlan
from model_orchestrator.registry import ClientKey, Registry

from .support import route_target

A, B = "http://backend-a.invalid:8000", "http://backend-b.invalid:8000"


async def answer(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/models":
        return httpx.Response(200, json={"data": [{"id": "org/model"}]})
    return httpx.Response(200, json={"ok": True})


class Split(ClientPool):
    """Hands out clients that only work for the plane they were asked for."""

    def __init__(self) -> None:
        super().__init__(lambda key: httpx.MockTransport(answer))
        self.traffic_calls = 0
        self.control_calls = 0

    def get(self, key: ClientKey) -> httpx.AsyncClient:
        self.traffic_calls += 1
        return super().get(key)

    def control(self, key: ClientKey) -> httpx.AsyncClient:
        self.control_calls += 1
        return super().control(key)


def pool_limit(client: httpx.AsyncClient) -> int:
    return client._transport._pool._max_connections  # type: ignore[attr-defined]


def test_traffic_and_control_are_different_clients() -> None:
    pool = ClientPool()
    key = ClientKey()
    assert pool.get(key) is pool.get(key)
    assert pool.control(key) is pool.control(key)
    assert pool.get(key) is not pool.control(key)
    proxied = ClientKey(proxy="http://proxy.invalid:3128")
    assert pool.get(proxied) is not pool.get(key) and pool.control(proxied) is not pool.control(key)


def test_traffic_pool_is_bounded_by_the_setting_and_control_by_the_config() -> None:
    pool = ClientPool(max_connections=7)
    targets = [route_target("One", backends=(A, B)), route_target("Two", backends=(A,))]
    pool.ensure(targets)
    key = ClientKey()
    assert pool_limit(pool.get(key)) == 7
    assert pool_limit(pool.control(key)) == control_pool_size(3) == 11
    assert ClientPool().get(key)._transport._pool._max_connections == 1000  # type: ignore[attr-defined]


def test_control_pool_size_leaves_room_for_every_backend() -> None:
    assert (
        control_pool_size(0) == control_pool_size(1) == control_pool_size(2) == CONTROL_MIN_CONNECTIONS == 8
    )
    for backends in (3, 10, 50, 400):
        assert control_pool_size(backends) >= 3 * backends, (
            "a probe, an extra probe and a discovery call each"
        )


async def test_a_config_with_more_backends_gets_a_bigger_control_pool() -> None:
    pool = ClientPool()
    key = ClientKey()
    pool.ensure([route_target(backends=(A,))])
    small = pool.control(key)
    pool.ensure([route_target(backends=(A,))])
    assert pool.control(key) is small, "unchanged config: same client, warm connections"
    many = tuple(f"http://backend-{n}.invalid:8000" for n in range(20))
    pool.ensure([route_target(backends=many)])
    big = pool.control(key)
    assert big is not small and pool_limit(big) == control_pool_size(20)
    assert not small.is_closed, "a probe may still be using the old one"
    await pool.aclose()
    assert small.is_closed and big.is_closed


def test_waiting_for_a_pooled_connection_has_its_own_short_timeout() -> None:
    timeout = request_timeout(600.0)
    assert (timeout.read, timeout.connect, timeout.pool) == (600.0, 10.0, POOL_TIMEOUT_S)
    assert POOL_TIMEOUT_S == 5.0


async def test_probes_and_discovery_use_the_control_client_and_traffic_does_not() -> None:
    target = route_target(backends=(A, B), aliases=("alias",))
    pool = Split()
    registry = Registry([target])
    monitor = HealthMonitor(fails=2, unhealthy_seconds=30, gating=True)
    monitor.configure([target])

    await HealthProber(monitor, pool, interval_s=10, timeout_s=1).probe_all()
    assert monitor.status(target, A) == monitor.status(target, B) == UP
    assert (pool.control_calls, pool.traffic_calls) == (2, 0), "probes: control only"

    assert await Discoverer(registry, monitor, pool).discover([target]) is True
    assert pool.control_calls == 3 and pool.traffic_calls == 0, "discovery: control only"

    entry = registry.table.get("alias")
    assert entry is not None
    record = RequestRecord(request_id="r", method="POST", path="/v1/chat/completions")

    async def receive() -> dict[str, str]:
        raise AssertionError("not reached")

    plan = ForwardPlan(
        entry=entry, method="POST", path="/v1/chat/completions", read_timeout_s=5, json_body=b"{}"
    )
    response = await Forwarder(pool, monitor, Metrics(version="t", git_sha="t")).forward(
        plan, record, receive
    )
    await record.run_cleanups()
    assert response.status_code == 200
    assert pool.control_calls == 3 and pool.traffic_calls == 1, "forwarding: traffic only"
    await pool.aclose()


@pytest.mark.parametrize("plane", ["traffic", "control"])
async def test_unknown_keys_are_created_on_demand(plane: str) -> None:
    pool = ClientPool(max_connections=3)
    key = ClientKey(proxy="socks5h://127.0.0.1:1080")
    client = pool.get(key) if plane == "traffic" else pool.control(key)
    assert pool_limit(client) == (3 if plane == "traffic" else CONTROL_MIN_CONNECTIONS)
    await pool.aclose()
