"""Discovery: which backend is asked, what an answer changes, when to ask again (design 7.3)."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from model_orchestrator.clients import ClientPool
from model_orchestrator.discovery import RETRY_LADDER_S, Discoverer, fetch_models, retry_delay
from model_orchestrator.health import HealthMonitor
from model_orchestrator.registry import Registry, Target

from .support import route_target, upstream_target

A, B = "http://a.invalid:1", "http://b.invalid:2"
Answer = Callable[[httpx.Request], httpx.Response]


def models(*ids: str) -> httpx.Response:
    return httpx.Response(200, json={"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})


class Net:
    def __init__(self, answers: dict[str, Answer | Exception]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, str | None]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        base = f"{request.url.scheme}://{request.url.host}:{request.url.port}"
        self.asked.append((base, request.headers.get("authorization")))
        assert request.url.path == "/v1/models"
        answer = self.answers[base]
        if isinstance(answer, Exception):
            raise answer
        return answer(request)


def rig(targets: list[Target], net: Net) -> tuple[Registry, HealthMonitor, Discoverer, list[int]]:
    registry = Registry(targets)
    monitor = HealthMonitor(fails=3, unhealthy_seconds=30, gating=True)
    monitor.configure(targets)
    changes: list[int] = []
    clients = ClientPool(lambda key: httpx.MockTransport(net))
    return (
        registry,
        monitor,
        Discoverer(registry, monitor, clients, on_change=lambda: changes.append(1)),
        changes,
    )


# -- fetch_models -----------------------------------------------------------------------


async def fetch(answer: Answer | Exception, api_key: str | None = None):
    net = Net({A: answer})
    async with httpx.AsyncClient(transport=httpx.MockTransport(net)) as client:
        return await fetch_models(client, A, api_key), net


async def test_fetch_models_parses_the_data_list_and_sends_the_backend_key() -> None:
    listing, net = await fetch(lambda r: models("m1", "m2"), api_key="backend-key")
    assert (
        listing.answered and listing.models is not None and [m["id"] for m in listing.models] == ["m1", "m2"]
    )
    assert net.asked == [(A, "Bearer backend-key")]


async def test_fetch_models_empty_list_is_a_listing() -> None:
    for payload in ({"data": []}, {"data": None}, {"object": "list"}):
        listing, _ = await fetch(lambda r, p=payload: httpx.Response(200, json=p))
        assert listing.answered and listing.models == []


async def test_fetch_models_without_a_usable_listing() -> None:
    for answer in (
        lambda r: httpx.Response(404, json={"detail": "Not Found"}),
        lambda r: httpx.Response(500),
        lambda r: httpx.Response(200, text="<html>"),
        lambda r: httpx.Response(200, json={"data": "nope"}),
    ):
        listing, _ = await fetch(answer)
        assert listing.answered and listing.models is None and listing.error


async def test_fetch_models_unreachable() -> None:
    listing, _ = await fetch(httpx.ConnectError("refused"))
    assert not listing.answered and listing.models is None and listing.error == "ConnectError"


# -- one discovery round --------------------------------------------------------------------


async def test_discovered_ids_enter_the_table() -> None:
    target = route_target(backends=(A,), aliases=("alias",))
    registry, _, discoverer, changes = rig([target], Net({A: lambda r: models("org/model")}))
    assert await discoverer.discover([target]) is True
    assert list(registry.table.entries) == ["alias", "org/model"]
    assert changes == [1]
    assert await discoverer.discover([target]) is False, "nothing new: no rebuild"
    assert changes == [1]


async def test_metadata_churn_is_not_announced_as_a_change() -> None:
    """Every vLLM listing differs in ``created``: no rebuild log line and no cache clearing for that."""
    created = iter(range(1000, 2000))

    def listing_like_vllm(request: httpx.Request) -> httpx.Response:
        stamp = next(created)
        entry = {"id": "org/model", "created": stamp, "permission": [{"id": f"modelperm-{stamp}"}]}
        return httpx.Response(200, json={"data": [entry]})

    target = route_target(backends=(A,))
    registry, _, discoverer, changes = rig([target], Net({A: listing_like_vllm}))
    assert await discoverer.discover([target]) is True
    for _ in range(5):
        assert await discoverer.discover([target]) is False
    assert changes == [1], "announced once, when the id appeared"
    entry = registry.table.get("org/model")
    assert entry is not None and entry.meta is not None and entry.meta["created"] == 1005


async def test_routes_sharing_a_backend_are_asked_once_per_round() -> None:
    chat = route_target("Chat", backends=(A,))
    tutor = route_target("Tutor", backends=(A,))
    vision = route_target("Vision", backends=(A,), route_type="vision")
    keyed = route_target("Keyed", backends=(A,), api_key="other-key")
    net = Net({A: lambda r: models("org/model")})
    registry, _, discoverer, _ = rig([chat, tutor, vision, keyed], net)
    await discoverer.discover(registry.targets)
    assert sorted(net.asked, key=str) == [(A, "Bearer other-key"), (A, None)]
    entry = registry.table.get("org/model")
    assert entry is not None and entry.route_name == "Chat"


async def test_in_a_pool_the_next_backend_is_asked_when_one_does_not_answer() -> None:
    target = route_target(backends=(A, B))
    net = Net({A: httpx.ConnectError("refused"), B: lambda r: models("org/model")})
    registry, _, discoverer, _ = rig([target], net)
    await discoverer.discover([target])
    assert [base for base, _ in net.asked] == [A, B]
    assert registry.table.get("org/model") is not None


async def test_in_a_pool_asking_stops_at_the_first_answer() -> None:
    target = route_target(backends=(A, B))
    net = Net({A: lambda r: httpx.Response(404), B: lambda r: models("org/model")})
    registry, _, discoverer, _ = rig([target], net)
    await discoverer.discover([target])
    assert [base for base, _ in net.asked] == [A], "a 404 is an answer: this pool has no models endpoint"
    assert registry.table.entries == {}


async def test_backends_that_are_down_are_not_asked() -> None:
    target = route_target(backends=(A, B))
    net = Net({A: lambda r: models("from-a"), B: lambda r: models("from-b")})
    registry, monitor, discoverer, _ = rig([target], net)
    key = monitor.probe_key(target, A)
    assert key is not None
    monitor.record_probe(key, False)
    await discoverer.discover([target])
    assert [base for base, _ in net.asked] == [B]


async def test_nothing_is_asked_when_every_backend_is_down() -> None:
    target = route_target(backends=(A,))
    net = Net({A: lambda r: models("m")})
    registry, monitor, discoverer, _ = rig([target], net)
    key = monitor.probe_key(target, A)
    assert key is not None
    monitor.record_probe(key, False)
    assert await discoverer.discover([target]) is False
    assert net.asked == []


async def test_an_id_seen_before_survives_an_outage_and_a_missing_listing() -> None:
    """C7: a discovered id that was seen before is 503 while its backend is down, not 404."""
    target = route_target(backends=(A,))
    net = Net({A: lambda r: models("org/model")})
    registry, _, discoverer, _ = rig([target], net)
    await discoverer.discover([target])
    for failure in (
        httpx.ConnectError("refused"),
        lambda r: httpx.Response(503),
        lambda r: httpx.Response(200, text="oops"),
    ):
        net.answers[A] = failure
        assert await discoverer.discover([target]) is False
        entry = registry.table.get("org/model")
        assert entry is not None and entry.listed
    state = registry.discovery_state(target)
    assert state.last_error and state.last_success is not None


async def test_an_id_the_backend_no_longer_lists_becomes_unlisted() -> None:
    target = route_target(backends=(A,))
    net = Net({A: lambda r: models("old", "kept")})
    registry, _, discoverer, changes = rig([target], net)
    await discoverer.discover([target])
    net.answers[A] = lambda r: models("kept", "new")
    assert await discoverer.discover([target]) is True
    old, new = registry.table.get("old"), registry.table.get("new")
    assert old is not None and not old.listed
    assert new is not None and new.listed
    assert len(changes) == 2


async def test_upstream_ids_with_filters() -> None:
    upstream = upstream_target(backends=(A,), exclude=("secret-*",))
    net = Net(
        {
            A: lambda r: httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "gemma", "x_router": {"route": "Gemma", "type": "chat"}},
                        {"id": "secret-model"},
                    ]
                },
            )
        }
    )
    registry, _, discoverer, _ = rig([upstream], net)
    await discoverer.discover([upstream])
    assert net.asked == [(A, "Bearer upstream-key")]
    assert list(registry.table.entries) == ["gemma"]
    entry = registry.table.get("gemma")
    assert entry is not None and entry.route_name == "Gemma" and entry.route_type == "chat"


async def test_a_target_replaced_during_discovery_is_not_updated() -> None:
    old = route_target(backends=(A,))
    registry, _, discoverer, _ = rig([old], Net({A: lambda r: models("late")}))
    registry.replace_targets([route_target(backends=(B,))])
    assert await discoverer.discover([old]) is False
    assert registry.table.entries == {}


# -- when to ask again -----------------------------------------------------------------------


def test_retry_delay() -> None:
    assert retry_delay(0, 300.0) == 300.0
    assert [retry_delay(n, 300.0) for n in (1, 2, 3, 4, 5, 50)] == [15.0, 30.0, 60.0, 120.0, 120.0, 120.0]
    assert RETRY_LADDER_S == (15.0, 30.0, 60.0, 120.0)
    assert [retry_delay(n, 60.0) for n in (1, 2, 3, 4)] == [15.0, 30.0, 60.0, 60.0], (
        "never slower than the interval"
    )
    assert retry_delay(1, 5.0) == 5.0


async def test_due_times_follow_the_outcome() -> None:
    import asyncio

    answered = route_target("Answered", backends=(A,), discovery_interval=300.0)
    silent = route_target("Silent", backends=(B,), discovery_interval=300.0, health_path=None)
    net = Net({A: lambda r: models("m"), B: httpx.ConnectError("refused")})
    registry, _, discoverer, _ = rig([answered, silent], net)
    now = asyncio.get_running_loop().time()
    await discoverer.discover(registry.targets)
    assert 299 <= discoverer._due[answered.ident] - now <= 301
    assert 14 <= discoverer._due[silent.ident] - now <= 16, "unreachable: asked again soon"
    await discoverer.discover([silent])
    assert 29 <= discoverer._due[silent.ident] - now <= 31
    discoverer.trigger([answered])
    assert discoverer._due[answered.ident] == 0.0 and discoverer._wake.is_set()
