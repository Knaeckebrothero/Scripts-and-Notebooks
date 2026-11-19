"""C13: the per-key concurrency cap (``api_keys.max_concurrency``, NULL = no cap)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.harness import world as w
from tests.harness.util import error_of, wait_until

CHAT = "/v1/chat/completions"
HOLD = 0.6  # seconds the backend keeps a request in flight
pytestmark = pytest.mark.change("C13")


@pytest.fixture(scope="module")
def capped(_stack):
    """Fresh capped keys for this module: ``capped[n]`` is a list of keys with max_concurrency n."""
    keys = {
        1: [_stack.db.create_key(max_concurrency=1) for _ in range(4)],
        2: [_stack.db.create_key(max_concurrency=2)],
    }
    _stack.sync_keys()
    return keys


def in_flight(fake) -> int:
    return len([call for call in fake.calls(CHAT) if call.finished is None])


def assert_concurrency_429(response) -> None:
    assert response.status_code == 429, f"expected 429, got {response.status_code}: {response.text[:200]}"
    error = error_of(response)
    assert error.get("code") == "concurrency_limit_exceeded"
    assert error["type"] == "rate_limit_error"
    assert response.headers.get("retry-after") == "1"


def test_cap_rejects_a_request_while_another_is_in_flight(stack, world, capped):
    key = capped[1][0]
    world.chat.delay(HOLD)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(stack.chat, w.CHAT_ALIAS, key=key)
        wait_until(
            lambda: in_flight(world.chat) == 1, timeout=3, what="the first request to reach the backend"
        )
        second = stack.chat(w.CHAT_ALIAS, key=key)
        assert first.result(timeout=10).status_code == 200

    assert_concurrency_429(second)
    assert len(world.chat.calls(CHAT)) == 1  # the rejected request was not forwarded
    world.chat.clear_faults()
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 200  # the slot is free again


def test_cap_of_two_admits_two(stack, world, capped):
    key = capped[2][0]
    world.chat.delay(HOLD)

    with ThreadPoolExecutor(2) as pool:
        running = [pool.submit(stack.chat, w.CHAT_ALIAS, key=key) for _ in range(2)]
        wait_until(lambda: in_flight(world.chat) == 2, timeout=3, what="two requests in flight")
        third = stack.chat(w.CHAT_ALIAS, key=key)
        assert [f.result(timeout=10).status_code for f in running] == [200, 200]

    assert_concurrency_429(third)


def test_a_stream_holds_its_slot_until_it_ends(stack, world, capped):
    key = capped[1][1]
    world.chat.pause_stream_after(2)

    with ThreadPoolExecutor(1) as pool:
        streaming = pool.submit(stack.chat_stream, w.CHAT_ALIAS, key=key)
        wait_until(lambda: len(world.chat.last(CHAT).sent) > 0, timeout=3, what="the stream to start")
        during = stack.chat(w.CHAT_ALIAS, key=key)
        world.chat.resume_stream()
        assert streaming.result(timeout=10).status == 200

    assert_concurrency_429(during)
    after = wait_until(
        lambda: (r := stack.chat(w.CHAT_ALIAS, key=key)).status_code == 200 and r,
        timeout=3, what="the slot to be released after the stream ended",
    )  # fmt: skip
    assert after.status_code == 200


def test_the_cap_is_per_key_and_keys_without_one_are_unaffected(stack, world, capped):
    capped_key = capped[1][2]
    world.chat.delay(HOLD)

    with ThreadPoolExecutor(4) as pool:
        first = pool.submit(stack.chat, w.CHAT_ALIAS, key=capped_key)
        wait_until(lambda: in_flight(world.chat) == 1, timeout=3, what="the capped key's request in flight")
        uncapped = [pool.submit(stack.chat, w.CHAT_ALIAS) for _ in range(3)]  # default key: no cap
        rejected = stack.chat(w.CHAT_ALIAS, key=capped_key)
        assert [f.result(timeout=10).status_code for f in uncapped] == [200, 200, 200]
        assert first.result(timeout=10).status_code == 200

    assert_concurrency_429(rejected)


def test_a_request_rejected_by_the_cap_frees_nothing_and_blocks_nothing(stack, world, capped):
    """Rejections must not leak slots: after several of them the key still has exactly one."""
    key = capped[1][3]
    world.chat.delay(HOLD)

    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(stack.chat, w.CHAT_ALIAS, key=key)
        wait_until(lambda: in_flight(world.chat) == 1, timeout=3, what="the first request in flight")
        rejected = [stack.chat(w.CHAT_ALIAS, key=key) for _ in range(3)]
        assert first.result(timeout=10).status_code == 200

    for response in rejected:
        assert_concurrency_429(response)
    world.chat.clear_faults()
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 200
