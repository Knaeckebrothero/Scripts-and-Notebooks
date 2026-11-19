"""The per-minute window (keyed by key id) and the concurrency cap (design 7.4)."""

from __future__ import annotations

from model_orchestrator.limits import ConcurrencyLimiter, RateLimiter


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_limit_then_rejects() -> None:
    clock = Clock()
    limiter = RateLimiter(clock)
    decisions = [limiter.check(1, 3) for _ in range(5)]
    assert [d.allowed for d in decisions] == [True, True, True, False, False]
    assert [d.remaining for d in decisions] == [2, 1, 0, 0, 0]
    assert all(d.limit == 3 for d in decisions)


def test_headers() -> None:
    clock = Clock(1_000_000.0)
    decision = RateLimiter(clock).check(1, 100)
    assert decision.headers() == {
        "X-RateLimit-Limit": "100",
        "X-RateLimit-Remaining": "99",
        "X-RateLimit-Reset": "1000060",
    }


def test_reset_is_when_the_oldest_request_leaves_the_window() -> None:
    clock = Clock(1_000_000.0)
    limiter = RateLimiter(clock)
    limiter.check(1, 2)
    clock.now += 10
    assert limiter.check(1, 2).reset == 1_000_060
    clock.now += 5
    rejected = limiter.check(1, 2)
    assert not rejected.allowed and rejected.reset == 1_000_060


def test_window_slides() -> None:
    clock = Clock()
    limiter = RateLimiter(clock)
    assert limiter.check(1, 2).allowed
    clock.now += 30
    assert limiter.check(1, 2).allowed
    assert not limiter.check(1, 2).allowed
    clock.now += 30.001  # the first request is now older than 60 s
    again = limiter.check(1, 2)
    assert again.allowed and again.remaining == 0
    assert not limiter.check(1, 2).allowed
    clock.now += 60
    assert limiter.check(1, 2).remaining == 1


def test_a_request_exactly_60_seconds_old_no_longer_counts() -> None:
    clock = Clock()
    limiter = RateLimiter(clock)
    limiter.check(1, 1)
    clock.now += 60
    assert limiter.check(1, 1).allowed


def test_rejected_requests_do_not_consume_the_budget() -> None:
    clock = Clock()
    limiter = RateLimiter(clock)
    limiter.check(1, 1)
    for _ in range(50):
        clock.now += 1
        assert not limiter.check(1, 1).allowed
    clock.now += 10.5  # 60.5 s after the one counted request
    assert limiter.check(1, 1).allowed


def test_keys_are_independent() -> None:
    limiter = RateLimiter(Clock())
    assert limiter.check(1, 1).allowed
    assert not limiter.check(1, 1).allowed
    assert limiter.check(2, 1).allowed


def test_a_changed_limit_applies_at_once() -> None:
    limiter = RateLimiter(Clock())
    for _ in range(3):
        limiter.check(1, 10)
    assert not limiter.check(1, 3).allowed
    assert limiter.check(1, 5).remaining == 1


def test_zero_limit_rejects_everything() -> None:
    clock = Clock(500.0)
    decision = RateLimiter(clock).check(1, 0)
    assert not decision.allowed and decision.remaining == 0 and decision.reset == 560


def test_sweep_forgets_idle_keys_only() -> None:
    clock = Clock()
    limiter = RateLimiter(clock)
    limiter.check(1, 10)
    clock.now += 45
    limiter.check(2, 10)
    clock.now += 30
    assert limiter.sweep() == 1
    assert limiter.tracked_keys() == 1
    assert limiter.check(2, 10).remaining == 8


# -- concurrency ----------------------------------------------------------------------


def test_no_cap_means_no_limit() -> None:
    limiter = ConcurrencyLimiter()
    assert all(limiter.try_acquire(1, None) for _ in range(1000))
    assert limiter.in_flight(1) == 1000


def test_cap_is_enforced_and_released() -> None:
    limiter = ConcurrencyLimiter()
    assert limiter.try_acquire(1, 2)
    assert limiter.try_acquire(1, 2)
    assert not limiter.try_acquire(1, 2)
    assert limiter.in_flight(1) == 2  # the rejected request holds nothing
    limiter.release(1)
    assert limiter.try_acquire(1, 2)
    assert not limiter.try_acquire(1, 2)


def test_caps_are_per_key() -> None:
    limiter = ConcurrencyLimiter()
    assert limiter.try_acquire(1, 1)
    assert limiter.try_acquire(2, 1)
    assert not limiter.try_acquire(1, 1)
    assert limiter.total() == 2


def test_release_never_goes_negative_and_forgets_idle_keys() -> None:
    limiter = ConcurrencyLimiter()
    limiter.release(7)
    assert limiter.in_flight(7) == 0
    limiter.try_acquire(7, 1)
    limiter.release(7)
    limiter.release(7)
    assert limiter.in_flight(7) == 0 and limiter.total() == 0
    assert limiter.try_acquire(7, 1)


def test_a_cap_lowered_below_what_is_in_flight_rejects_until_it_drains() -> None:
    limiter = ConcurrencyLimiter()
    for _ in range(3):
        limiter.try_acquire(1, None)
    assert not limiter.try_acquire(1, 2)
    limiter.release(1)
    limiter.release(1)
    assert limiter.try_acquire(1, 2)


def test_zero_cap_rejects_everything() -> None:
    assert not ConcurrencyLimiter().try_acquire(1, 0)
