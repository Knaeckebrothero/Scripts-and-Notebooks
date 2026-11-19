"""Section 5 "Rate limit": per-key requests per minute, sliding 60 s window, 429, headers. C19 (headers)."""

from __future__ import annotations

import os
import time

import pytest

from tests.harness import world as w
from tests.harness.util import error_of, wait_until

CHAT = "/v1/chat/completions"
RL_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")


def limits(response) -> tuple[int, int, int]:
    for header in RL_HEADERS:
        assert header in response.headers, (
            f"{header} missing on {response.status_code}: {dict(response.headers)}"
        )
    return tuple(int(response.headers[h]) for h in RL_HEADERS)


@pytest.fixture(scope="module")
def rl_keys(_stack):
    """One fresh key per test (limits are per key and live in the router's memory)."""
    db = _stack.db
    made = {
        "three": db.create_key(rpm=3),
        "three-b": db.create_key(rpm=3),
        "two-mixed": db.create_key(rpm=2),
        "two-rejects": db.create_key(rpm=2),
        "two-errors": db.create_key(rpm=2),
        "two-stream": db.create_key(rpm=2),
        "one-raise": db.create_key(rpm=1),
        "five": db.create_key(rpm=5),
        "c19": db.create_key(rpm=50),
        "c19-legacy": db.create_key(rpm=50),
    }
    _stack.sync_keys()
    return made


def test_successful_response_carries_the_limit_headers(stack, world, rl_keys):
    before = time.time()

    r = stack.chat(w.CHAT_ALIAS, key=rl_keys["five"])

    assert r.status_code == 200
    limit, remaining, reset = limits(r)
    assert (limit, remaining) == (5, 4)
    # the window is 60 s long and starts with this key's oldest counted request
    assert before + 58 <= reset <= time.time() + 61


def test_remaining_counts_down_then_429(stack, world, rl_keys):
    key = rl_keys["three"]
    first_at = time.time()

    remaining = [limits(stack.chat(w.CHAT_ALIAS, key=key))[1] for _ in range(3)]
    rejected = stack.chat(w.CHAT_ALIAS, key=key)

    assert remaining == [2, 1, 0]
    assert rejected.status_code == 429
    assert error_of(rejected)["type"] == "rate_limit_error"
    limit, left, reset = limits(rejected)
    assert (limit, left) == (3, 0)
    assert first_at + 58 <= reset <= first_at + 62  # when the oldest request leaves the window
    assert len(world.chat.calls(CHAT)) == 3  # the rejected request never reached the backend


def test_rejected_requests_do_not_extend_the_window(stack, world, rl_keys):
    key = rl_keys["three-b"]
    for _ in range(3):
        assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 200

    resets = set()
    for _ in range(5):
        rejected = stack.chat(w.CHAT_ALIAS, key=key)
        assert rejected.status_code == 429
        resets.add(limits(rejected)[2])

    assert len(resets) == 1  # still tied to the three accepted requests


def test_limit_is_per_key(stack, world, rl_keys):
    """Another key is untouched by the first key running dry."""
    key = rl_keys["three"]
    while stack.chat(w.CHAT_ALIAS, key=key).status_code != 429:
        pass

    assert stack.chat(w.CHAT_ALIAS).status_code == 200


def test_limit_is_shared_by_all_endpoints(stack, world, rl_keys):
    key = rl_keys["two-mixed"]

    one = stack.chat(w.CHAT_ALIAS, key=key)
    two = stack.post("/v1/embeddings", key=key, json={"model": w.EMBED_ALIAS, "input": "x"})
    three = stack.post(
        "/v1/rerank", key=key, json={"model": w.RERANK_ALIAS, "query": "q", "documents": ["a"]}
    )

    assert (one.status_code, two.status_code, three.status_code) == (200, 200, 429)
    assert limits(two)[1] == 0
    assert not world.rerank.calls("/v1/rerank")


def test_streaming_requests_count_and_carry_the_headers(stack, world, rl_keys):
    key = rl_keys["two-stream"]

    first = stack.chat_stream(w.CHAT_ALIAS, key=key)
    second = stack.chat_stream(w.CHAT_ALIAS, key=key)
    third = stack.chat(w.CHAT_ALIAS, body={"stream": True}, key=key)

    assert (first.status, second.status) == (200, 200)
    assert first.headers["x-ratelimit-limit"] == "2"
    assert [first.headers["x-ratelimit-remaining"], second.headers["x-ratelimit-remaining"]] == ["1", "0"]
    assert third.status_code == 429
    assert third.headers["content-type"].startswith("application/json")
    assert limits(third)[:2] == (2, 0)


def test_requests_rejected_before_the_limiter_cost_nothing(stack, world, rl_keys):
    """404 (unknown model) and 400 (wrong endpoint) are decided before the limiter runs."""
    key = rl_keys["two-rejects"]
    for _ in range(4):
        assert stack.chat("no-such-model", key=key).status_code == 404
        assert stack.chat(w.EMBED_ALIAS, key=key).status_code == 400

    assert limits(stack.chat(w.CHAT_ALIAS, key=key))[1] == 1
    assert limits(stack.chat(w.CHAT_ALIAS, key=key))[1] == 0


def test_forwarded_requests_count_even_when_the_backend_fails(stack, world, rl_keys):
    key = rl_keys["two-errors"]
    world.chat.fail(400, times=1)

    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 400
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 200
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 429


def test_a_raised_limit_is_picked_up_from_the_database(stack, world, rl_keys):
    key = rl_keys["one-raise"]
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 200
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 429

    stack.db.update_key(key, rate_limit_rpm=10)

    ok = wait_until(
        lambda: (r := stack.chat(w.CHAT_ALIAS, key=key)).status_code == 200 and r,
        timeout=5, what="the raised limit to apply",
    )  # fmt: skip
    assert limits(ok)[0] == 10
    assert limits(ok)[1] == 8  # the two requests made under the old limit still count


@pytest.mark.change("C19")
def test_limit_headers_are_on_error_responses_once_the_limiter_ran(stack, world, rl_keys):
    key = rl_keys["c19"]
    world.chat.fail(400, times=1)  # the first request gets this ...
    world.chat.fail(500, times=1)  # ... and the second one this

    bad_request = stack.chat(w.CHAT_ALIAS, key=key)
    server_error = stack.chat(w.CHAT_ALIAS, key=key)

    assert bad_request.status_code == 400
    assert limits(bad_request)[:2] == (50, 49)
    assert server_error.status_code >= 500
    assert limits(server_error)[:2] == (50, 48)


@pytest.mark.legacy_only
def test_limit_headers_are_missing_on_error_responses_today(stack, world, rl_keys):
    world.chat.fail(400, times=1)

    r = stack.chat(w.CHAT_ALIAS, key=rl_keys["c19-legacy"])

    assert r.status_code == 400
    assert not [h for h in RL_HEADERS if h in r.headers]


@pytest.mark.timeout(120)
@pytest.mark.skipif(not os.environ.get("E2E_SLOW"), reason="waits a full rate-limit window; set E2E_SLOW=1")
def test_the_window_slides_after_sixty_seconds(stack, world):
    key = stack.new_key(rpm=2)
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 200
    time.sleep(20)
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 200
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 429

    time.sleep(41)  # the first request is now older than 60 s, the second is not

    assert limits(stack.chat(w.CHAT_ALIAS, key=key))[1] == 0
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 429
