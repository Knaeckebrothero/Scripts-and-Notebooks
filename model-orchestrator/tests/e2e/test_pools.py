"""Pools (``backend:`` as a list): round robin, failover, passive unhealthy marks. C21, C22. Also ``proxy:``."""

from __future__ import annotations

import time

import pytest

from tests.fakes import FakeVLLM
from tests.harness.stack import route, user
from tests.harness.util import error_of, wait_until

CHAT = "/v1/chat/completions"
MODEL = "acme/pooled"


@pytest.fixture
def pool(factory):
    one, two = factory.fake("vllm", [MODEL], name="one"), factory.fake("vllm", [MODEL], name="two")
    stack = factory.stack(
        [route("Pool", "chat", [one, two], backend_model_name=MODEL, models=["pooled"])], name="pool"
    )
    return stack, one, two


def counts(*fakes) -> list[int]:
    return [len(fake.calls(CHAT)) for fake in fakes]


def test_requests_are_spread_over_the_pool(pool):
    stack, one, two = pool

    for _ in range(8):
        assert stack.chat("pooled").status_code == 200

    assert counts(one, two) == [4, 4]


def test_streams_are_spread_over_the_pool_too(pool):
    stack, one, two = pool

    for _ in range(4):
        assert stack.chat_stream("pooled").status == 200

    assert counts(one, two) == [2, 2]


def test_a_dead_backend_is_failed_over(pool):
    stack, one, two = pool
    one.stop()

    answers = [stack.chat("pooled", f"n{i}") for i in range(6)]

    assert [a.status_code for a in answers] == [200] * 6
    assert counts(two) == [6]


def test_a_backend_5xx_is_failed_over(pool):
    stack, one, two = pool
    one.fail(500)

    answers = [stack.chat("pooled") for _ in range(4)]

    assert [a.status_code for a in answers] == [200] * 4
    assert counts(two) == [4]
    assert counts(one)[0] >= 1  # it was tried, failed, and the request moved on


def test_a_backend_4xx_is_not_failed_over(pool):
    stack, one, two = pool
    one.fail(400, {"error": {"message": "bad request, do not retry", "type": "BadRequestError", "code": 400}})
    two.fail(400, {"error": {"message": "bad request, do not retry", "type": "BadRequestError", "code": 400}})

    r = stack.chat("pooled")

    assert r.status_code == 400
    assert "bad request, do not retry" in error_of(r)["message"]
    assert sum(counts(one, two)) == 1


def test_when_every_backend_fails_each_was_tried_once(pool):
    stack, one, two = pool
    one.fail(500)
    two.fail(502)

    r = stack.chat("pooled")

    assert r.status_code >= 500  # 503 today; the last backend's own status after C2
    error_of(r)
    assert counts(one, two) == [1, 1]


def test_a_failed_backend_is_avoided_then_used_again(pool):
    """Passive mark: after a failure a backend is skipped for ROUTER_UNHEALTHY_SECONDS (2 s here)."""
    stack, one, two = pool
    one.fail(500, times=1)
    assert stack.chat("pooled").status_code == 200  # hit `one`, failed over to `two`
    assert counts(one, two) == [1, 1]
    marked_at = time.monotonic()

    for _ in range(4):
        assert stack.chat("pooled").status_code == 200
    if time.monotonic() - marked_at < 1.5:
        assert counts(one, two) == [1, 5]  # all four went to the unmarked backend

    def one_is_back():
        assert stack.chat("pooled").status_code == 200
        return counts(one)[0] > 1

    wait_until(one_is_back, timeout=6, interval=0.2, what="the marked backend to get traffic again")


def test_when_all_backends_are_marked_all_are_still_used(pool):
    stack, one, two = pool
    one.fail(500, times=1)
    two.fail(500, times=1)
    assert stack.chat("pooled").status_code >= 500  # both tried, both marked

    answers = [stack.chat("pooled") for _ in range(4)]

    assert [a.status_code for a in answers] == [200] * 4
    assert all(n >= 2 for n in counts(one, two))  # one failure each, then traffic on both


def test_single_backend_route_keeps_being_tried_after_failures(factory):
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack([route("Solo", "chat", backend, models=["solo"])])
    backend.fail(500, times=2)

    statuses = [stack.chat("acme/solo").status_code for _ in range(3)]

    assert statuses[2] == 200 and statuses[0] >= 500 and statuses[1] >= 500
    assert counts(backend) == [3]


# ---- C21: streams fail over before the first byte -----------------------------


def full_stream(text: str = "hello") -> bytes:
    return b"".join(FakeVLLM.render_stream({"model": MODEL, "messages": user(text)}))


@pytest.mark.change("C21")
def test_streams_fail_over_from_a_dead_backend(pool):
    stack, one, two = pool
    one.stop()

    results = [stack.chat_stream("pooled") for _ in range(4)]

    assert [r.status for r in results] == [200] * 4
    assert [r.raw == full_stream() for r in results] == [True] * 4  # every client got the whole stream
    assert counts(two) == [4]


@pytest.mark.change("C21")
def test_streams_fail_over_from_a_backend_5xx(pool):
    stack, one, two = pool
    one.fail(503)

    results = [stack.chat_stream("pooled") for _ in range(4)]

    assert [r.status for r in results] == [200] * 4
    assert [r.raw == full_stream() for r in results] == [True] * 4
    assert counts(two) == [4]
    assert counts(one)[0] >= 1  # it was tried first at least once, and the stream moved on


def test_stream_that_already_started_is_not_retried(pool):
    """The limit of C21, true before and after: once a byte reached the client there is no second attempt."""
    stack, one, two = pool
    one.stream_gap = two.stream_gap = 0.01
    one.drop(after=2)
    sent_before_drop = b"".join(FakeVLLM.render_stream({"model": MODEL, "messages": user("hello")})[:2])

    results = [stack.chat_stream("pooled") for _ in range(2)]  # round robin: one request per backend

    assert counts(one, two) == [1, 1]
    broken = [r for r in results if r.raw != full_stream()]
    assert len(broken) == 1
    assert broken[0].status == 200 and broken[0].raw.startswith(sent_before_drop)


@pytest.mark.legacy_only
def test_streams_do_not_fail_over_today(pool):
    stack, one, two = pool
    one.stop()

    first = stack.chat_stream("pooled")  # round robin starts with the first backend, which is gone
    second = stack.chat_stream("pooled")

    assert first.status == 200 and "Backend unavailable" in first.text
    assert second.raw == b"".join(FakeVLLM.render_stream({"model": MODEL, "messages": user("hello")}))


# ---- C22: a dropped connection is a failed attempt ------------------------------


@pytest.mark.change("C22")
@pytest.mark.parametrize("fault", ["reset_connection", "drop"])
def test_a_backend_that_closes_the_connection_is_failed_over(pool, fault):
    stack, one, two = pool
    getattr(one, fault)()

    answers = [stack.chat("pooled") for _ in range(4)]

    assert [a.status_code for a in answers] == [200] * 4
    assert counts(two) == [4]
    assert counts(one)[0] >= 1


@pytest.mark.change("C22")
def test_when_every_backend_closes_the_connection_the_answer_is_502(pool):
    stack, one, two = pool
    one.reset_connection()
    two.drop()

    r = stack.chat("pooled", headers={"X-Request-ID": "c22-pool"})

    assert r.status_code == 502
    assert error_of(r).get("code") == "backend_disconnected"
    assert r.headers.get("x-router-error-source") == "router"
    assert r.headers.get("x-request-id") == "c22-pool"
    assert counts(one, two) == [1, 1]  # each was tried once


@pytest.mark.legacy_only
def test_a_backend_that_closes_the_connection_is_not_failed_over_today(pool):
    stack, one, two = pool
    one.reset_connection()

    first, second = stack.chat("pooled"), stack.chat("pooled")  # round robin: `one`, then `two`

    assert (first.status_code, first.text) == (500, "Internal Server Error")
    assert second.status_code == 200
    assert counts(one, two) == [1, 1]


# ---- proxy -----------------------------------------------------------------


def test_route_proxy_option_sends_traffic_through_the_proxy(factory):
    proxy = factory.forward_proxy()
    proxied, direct = (
        factory.fake("vllm", ["acme/far"], name="far"),
        factory.fake("vllm", ["acme/near"], name="near"),
    )
    stack = factory.stack(
        [
            route("Far", "chat", proxied, models=["far"], backend_model_name="acme/far", proxy=proxy.url),
            route("Near", "chat", direct, models=["near"], backend_model_name="acme/near"),
        ]
    )

    far, near = stack.chat("far", "via proxy"), stack.chat("near", "direct")

    assert (far.status_code, near.status_code) == (200, 200)
    assert far.content == FakeVLLM.render_completion({"model": "acme/far", "messages": user("via proxy")})
    assert [p.method for p in proxy.calls(CHAT)] == ["POST"]
    assert proxy.calls(CHAT)[0].url == f"{proxied.url}{CHAT}"
    # discovery for that route goes through the proxy as well; the other route never does
    assert proxy.calls("/v1/models") and "acme/far" in stack.models()
    assert not [p for p in proxy.requests if direct.url in p.url]
    assert "authorization" not in proxy.calls(CHAT)[0].headers
