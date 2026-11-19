"""Discovery refresh, and health-driven availability: C7 and design 7.3.

The first half shares one router per module, because the legacy router only
re-runs discovery 15 s after its start (its fixed warm-up ladder). The setup
waits for that once; the tests then only look at the result.
"""

from __future__ import annotations

import time

import pytest

from tests.harness.stack import route
from tests.harness.util import error_of, holds_for, wait_until

CHAT = "/v1/chat/completions"
SETTLE_TIMEOUT = 30  # legacy: discovery re-runs 15 s after start; new: probes and discovery take 2-3 s


class Site:
    """Two chat backends behind one router. ``down`` is stopped right after the router started."""

    def __init__(self, factory):
        self.up = factory.fake("vllm", ["acme/up-model"], name="up")
        self.down = factory.fake("vllm", ["acme/down-model"], name="down")
        self.stack = factory.stack(
            [
                route("Up", "chat", self.up, backend_model_name="acme/up-model", models=["up-alias"]),
                route("Down", "chat", self.down, backend_model_name="acme/down-model", models=["down-alias"]),
            ],
            name="availability",
        )
        assert {"acme/up-model", "acme/down-model", "up-alias", "down-alias"} <= set(self.stack.models())
        self.up.add_model("acme/late-arrival")
        self.down.stop()
        self.stopped_at = time.monotonic()
        # Both targets drop the discovered id from the listing once they noticed the backend is gone.
        wait_until(
            lambda: "acme/down-model" not in self.stack.models() and "acme/late-arrival" in self.stack.models(),
            timeout=SETTLE_TIMEOUT, interval=0.25, what="the router to notice the changes on its backends",
        )  # fmt: skip
        self.down.clear_requests()


@pytest.fixture(scope="module")
def site(module_factory):
    return Site(module_factory)


@pytest.mark.timeout(60)
def test_a_model_added_to_a_backend_later_is_discovered(site):
    assert "acme/late-arrival" in site.stack.models()

    r = site.stack.chat("acme/late-arrival")

    assert r.status_code == 200
    assert site.up.last(CHAT).json["model"] == "acme/late-arrival"


@pytest.mark.timeout(60)
def test_other_routes_are_unaffected_by_a_dead_backend(site):
    assert site.stack.chat("up-alias").status_code == 200
    assert site.stack.chat("acme/up-model").status_code == 200


# ---- C7 ------------------------------------------------------------------------


@pytest.mark.timeout(60)
@pytest.mark.change("C7")
def test_model_whose_backends_are_down_is_not_listed(site):
    listed = site.stack.models()

    assert "down-alias" not in listed
    assert "acme/down-model" not in listed
    assert "up-alias" in listed


@pytest.mark.timeout(60)
@pytest.mark.change("C7")
@pytest.mark.parametrize("model", ["down-alias", "acme/down-model"], ids=["alias", "discovered-id"])
def test_request_for_an_unavailable_model_is_an_immediate_503(site, model):
    started = time.monotonic()

    r = site.stack.chat(model)

    assert r.status_code == 503
    error = error_of(r)
    assert error.get("code") == "model_unavailable"
    assert error["type"] == "api_error"
    assert r.headers.get("retry-after") == "30"
    assert r.headers.get("x-router-error-source") == "router"
    assert time.monotonic() - started < 1.0
    assert not site.down.calls(CHAT)


@pytest.mark.timeout(60)
@pytest.mark.change("C7")
def test_streaming_request_for_an_unavailable_model_is_a_json_503(site):
    s = site.stack.chat_stream("down-alias")

    assert s.status == 503
    assert s.json()["error"].get("code") == "model_unavailable"
    assert s.headers.get("retry-after") == "30"


@pytest.mark.timeout(60)
@pytest.mark.change("C7")
@pytest.mark.parametrize("model", ["down-alias", "acme/down-model"], ids=["alias", "discovered-id"])
def test_get_model_says_503_for_a_known_but_unavailable_id(site, model):
    r = site.stack.get(f"/v1/models/{model}", key=None)

    assert r.status_code == 503
    assert error_of(r).get("code") == "model_unavailable"


@pytest.mark.timeout(60)
@pytest.mark.legacy_only
def test_down_model_stays_listed_and_fails_on_connect_today(site):
    assert "down-alias" in site.stack.models()

    r = site.stack.chat("down-alias")

    assert r.status_code == 503
    assert error_of(r)["message"].startswith("All backends unavailable: ConnectError")
    assert "retry-after" not in r.headers


@pytest.mark.timeout(60)
@pytest.mark.legacy_only
def test_discovered_id_is_404_while_its_backend_is_down_today(site):
    r = site.stack.chat("acme/down-model")

    assert r.status_code == 404
    assert error_of(r)["message"] == "Model 'acme/down-model' not found"


# ---- transitions and gating rules (new router only) -------------------------------

gating = pytest.mark.feature("health_gating")
GONE = 8  # seconds allowed for "two failed probes at a 1 s interval" to show
BACK = 8  # seconds allowed for one good probe plus discovery


def listed(stack, *ids: str) -> bool:
    return set(ids) <= set(stack.models())


def unlisted(stack, *ids: str) -> bool:
    return not set(ids) & set(stack.models())


@pytest.fixture
def one(factory):
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack([route("Solo", "chat", backend, backend_model_name="acme/solo", models=["solo"])])
    return stack, backend


@gating
def test_backend_goes_down_and_comes_back(one):
    stack, backend = one
    assert listed(stack, "solo", "acme/solo") and stack.chat("solo").status_code == 200

    backend.stop()
    wait_until(
        lambda: unlisted(stack, "solo", "acme/solo"), timeout=GONE, what="the model to leave /v1/models"
    )
    for model in ("solo", "acme/solo"):
        r = stack.chat(model)
        assert r.status_code == 503 and error_of(r).get("code") == "model_unavailable"
        assert r.headers.get("retry-after") == "30"

    backend.start()
    wait_until(
        lambda: listed(stack, "solo", "acme/solo"), timeout=BACK, what="the model to return to /v1/models"
    )
    assert stack.chat("solo").status_code == 200
    assert stack.chat("acme/solo").status_code == 200


@gating
def test_failing_health_endpoint_takes_the_model_out_even_if_requests_would_work(one):
    stack, backend = one
    backend.health_status = 503

    wait_until(lambda: unlisted(stack, "solo"), timeout=GONE, what="the model to leave /v1/models")
    backend.clear_requests()
    assert stack.chat("solo").status_code == 503
    assert not backend.calls(CHAT)  # not forwarded

    backend.health_status = 200
    wait_until(lambda: listed(stack, "solo"), timeout=BACK, what="the model to return")
    assert stack.chat("solo").status_code == 200


@gating
@pytest.mark.parametrize("status", [401, 404, 204])
def test_health_answers_below_500_count_as_up(one, status):
    """A backend whose health path is wrong or guarded still works and must stay in rotation."""
    stack, backend = one
    backend.health_status = status

    holds_for(
        lambda: listed(stack, "solo") and stack.chat("solo").status_code == 200, seconds=4.0, interval=0.5
    )
    assert len(backend.calls("/health")) >= 3  # it really was probed meanwhile


@gating
def test_hanging_health_endpoint_takes_the_model_out(one):
    stack, backend = one
    backend.hang(path="/health")

    wait_until(lambda: unlisted(stack, "solo"), timeout=GONE + 4, what="the model to leave /v1/models")


@gating
def test_one_failed_probe_is_not_enough(one):
    """ROUTER_HEALTH_FAILS is 2 here: a single blip must not take a model away."""
    stack, backend = one
    backend.fail(503, path="/health", times=1)

    holds_for(lambda: listed(stack, "solo"), seconds=4.0, interval=0.2, what="the model staying listed")
    assert stack.chat("solo").status_code == 200


@gating
def test_backend_errors_on_requests_never_make_a_model_unavailable(one):
    """Passive marks only steer selection inside a pool (design 7.3)."""
    stack, backend = one
    backend.fail(500, path=CHAT, times=5)

    statuses = [stack.chat("solo").status_code for _ in range(5)]

    assert all(s >= 500 for s in statuses)
    assert len(backend.calls(CHAT)) == 5  # every one was forwarded
    assert listed(stack, "solo")
    assert stack.chat("solo").status_code == 200


@gating
def test_route_without_a_probe_is_always_available(factory):
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack(
        [route("Solo", "chat", backend, backend_model_name="acme/solo", models=["solo"], health_path=None)]
    )
    backend.stop()

    holds_for(
        lambda: listed(stack, "solo"), seconds=3.5, interval=0.5, what="the unprobed route staying listed"
    )
    r = stack.chat("solo")
    assert r.status_code == 503
    assert error_of(r).get("code") == "backend_unreachable"  # forwarded and failed, not gated
    assert not backend.calls("/health")


@gating
def test_pool_stays_available_while_one_backend_is_up(factory):
    one_, two = (
        factory.fake("vllm", ["acme/pooled"], name="one"),
        factory.fake("vllm", ["acme/pooled"], name="two"),
    )
    stack = factory.stack(
        [route("Pool", "chat", [one_, two], backend_model_name="acme/pooled", models=["pooled"])]
    )

    one_.stop()
    time.sleep(3.0)  # long enough for the prober to have marked it down
    assert listed(stack, "pooled")
    assert [stack.chat("pooled").status_code for _ in range(4)] == [200] * 4

    two.stop()
    wait_until(lambda: unlisted(stack, "pooled"), timeout=GONE, what="the pool to become unavailable")
    assert stack.chat("pooled").status_code == 503


@gating
def test_backend_that_is_down_at_startup_is_unavailable_from_the_first_request(factory):
    """One probe round runs before the listener opens and is taken at face value."""
    backend = factory.fake("vllm", ["acme/solo"])
    other = factory.fake("vllm", ["acme/other"], name="other")
    backend.stop()
    stack = factory.stack(
        [
            route("Solo", "chat", backend, backend_model_name="acme/solo", models=["solo"]),
            route("Other", "chat", other, backend_model_name="acme/other", models=["other"]),
        ]
    )

    assert listed(stack, "other", "acme/other") and unlisted(stack, "solo")
    r = stack.chat("solo")
    assert r.status_code == 503 and error_of(r).get("code") == "model_unavailable"

    backend.start()
    wait_until(
        lambda: listed(stack, "solo", "acme/solo"), timeout=BACK, what="the late backend to be discovered"
    )
    assert stack.chat("acme/solo").status_code == 200


@gating
def test_id_that_a_live_backend_stops_listing_stays_known(one):
    """Discovered ids are remembered while their route is configured (design 7.3)."""
    stack, backend = one
    backend.add_model("acme/adapter")
    wait_until(lambda: listed(stack, "acme/adapter"), timeout=BACK, what="the new id to be discovered")

    backend.remove_model("acme/adapter")
    time.sleep(3.0)  # a discovery round or more

    assert stack.chat("acme/adapter").status_code != 404, "a discovered id was forgotten"


@gating
def test_gating_can_be_switched_off(factory):
    """ROUTER_HEALTH_GATING=off: probes still run, but nothing is taken out of rotation."""
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack(
        [route("Solo", "chat", backend, backend_model_name="acme/solo", models=["solo"])],
        env={"ROUTER_HEALTH_GATING": "off"},
    )
    backend.health_status = 503

    holds_for(
        lambda: listed(stack, "solo") and stack.chat("solo").status_code == 200, seconds=4.0, interval=0.5
    )
    assert len(backend.calls("/health")) >= 3  # probes kept running

    backend.stop()
    time.sleep(3.0)
    assert listed(stack, "solo")
    r = stack.chat("solo")
    assert r.status_code == 503
    assert error_of(r).get("code") == "backend_unreachable"  # forwarded, as before the rework
