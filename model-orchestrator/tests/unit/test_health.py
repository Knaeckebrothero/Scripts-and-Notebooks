"""Probe state, availability gating and backend selection (design 7.3)."""

from __future__ import annotations

from model_orchestrator.health import DOWN, UNKNOWN, UNPROBED, UP, BackendState, HealthMonitor

from .support import route_target, upstream_target

A, B, C = "http://a.invalid:1", "http://b.invalid:2", "http://c.invalid:3"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def monitor(
    *, fails: int = 3, unhealthy: float = 30.0, gating: bool = True, clock: Clock | None = None
) -> HealthMonitor:
    return HealthMonitor(fails=fails, unhealthy_seconds=unhealthy, gating=gating, clock=clock or Clock())


def probe(mon: HealthMonitor, target, backend: str, ok: bool) -> tuple[str, str]:
    key = mon.probe_key(target, backend)
    assert key is not None
    return mon.record_probe(key, ok, error=None if ok else "ConnectError")


# -- the state machine ---------------------------------------------------------------------


def test_first_result_is_taken_at_face_value() -> None:
    dead, alive = BackendState(), BackendState()
    assert dead.record(False, 3) == (UNKNOWN, DOWN)
    assert alive.record(True, 3) == (UNKNOWN, UP)


def test_down_only_after_consecutive_failures() -> None:
    state = BackendState()
    state.record(True, 3)
    assert state.record(False, 3) == (UP, UP)
    assert state.record(False, 3) == (UP, UP)
    assert state.record(False, 3) == (UP, DOWN)
    assert state.consecutive_failures == 3


def test_a_success_in_between_resets_the_count() -> None:
    state = BackendState()
    state.record(True, 3)
    for ok in (False, False, True, False, False):
        state.record(ok, 3)
    assert state.status == UP and state.consecutive_failures == 2
    assert state.record(False, 3) == (UP, DOWN)


def test_up_again_after_one_success() -> None:
    state = BackendState()
    state.record(False, 3)
    assert state.record(False, 3) == (DOWN, DOWN)
    assert state.record(True, 3) == (DOWN, UP)
    assert state.consecutive_failures == 0


def test_threshold_of_one() -> None:
    state = BackendState()
    state.record(True, 1)
    assert state.record(False, 1) == (UP, DOWN)


# -- availability -------------------------------------------------------------------------------


def test_a_route_without_health_path_is_never_probed_and_always_available() -> None:
    target = route_target(health_path=None, backends=(A, B))
    mon = monitor()
    assert mon.configure([target]) == []
    assert mon.probe_keys() == []
    assert mon.status(target, A) == UNPROBED
    assert mon.target_up(target) and mon.available(target)
    assert mon.candidates(target) == [A, B]


def test_unknown_counts_as_available() -> None:
    target = route_target(backends=(A,))
    mon = monitor()
    assert len(mon.configure([target])) == 1
    assert mon.status(target, A) == UNKNOWN and mon.available(target)


def test_pool_is_available_while_one_backend_is_not_down() -> None:
    target = route_target(backends=(A, B))
    mon = monitor()
    mon.configure([target])
    probe(mon, target, A, False)
    probe(mon, target, B, True)
    assert mon.status(target, A) == DOWN and mon.status(target, B) == UP
    assert mon.available(target)
    assert mon.candidates(target) == [B]
    assert [mon.select(target) for _ in range(3)] == [B, B, B]
    for _ in range(3):
        probe(mon, target, B, False)
    assert not mon.available(target) and not mon.target_up(target)
    assert mon.candidates(target) == [] and mon.select(target) is None
    probe(mon, target, A, True)
    assert mon.available(target) and mon.candidates(target) == [A]


def test_gating_off_keeps_reporting_but_never_makes_a_model_unavailable() -> None:
    target = route_target(backends=(A, B))
    mon = monitor(gating=False)
    mon.configure([target])
    probe(mon, target, A, False)
    probe(mon, target, B, False)
    assert mon.status(target, A) == DOWN and not mon.target_up(target), "state is still reported"
    assert mon.available(target)
    assert mon.candidates(target) == [A, B]
    assert mon.select(target) in (A, B)


def test_routes_sharing_a_backend_share_one_probe() -> None:
    chat = route_target("Chat", backends=(A,))
    vision = route_target("Vision", backends=(A,), route_type="vision")
    other_path = route_target("Other", backends=(A,), health_path="/ping")
    mon = monitor()
    assert len(mon.configure([chat, vision, other_path])) == 2
    key = mon.probe_key(chat, A)
    assert key is not None and key == mon.probe_key(vision, A)
    assert [t.name for t in mon.users_of(key)] == ["Chat", "Vision"]
    probe(mon, chat, A, False)
    assert not mon.available(chat) and not mon.available(vision)
    assert mon.available(other_path), "a different health path is a different probe"


def test_the_probe_sends_the_routes_backend_key() -> None:
    target = route_target(backends=(A,), api_key="backend-secret")
    mon = monitor()
    (key,) = mon.configure([target])
    assert mon.api_key_for(key) == "backend-secret"


def test_upstream_router_is_probed_like_a_backend() -> None:
    upstream = upstream_target()
    mon = monitor()
    (key,) = mon.configure([upstream])
    assert key[1] == "/livez"
    probe(mon, upstream, upstream.backends[0], False)
    assert not mon.available(upstream)


def test_reconfigure_keeps_state_of_probes_that_stay() -> None:
    old = route_target(backends=(A, B))
    mon = monitor()
    mon.configure([old])
    probe(mon, old, A, False)
    probe(mon, old, B, True)
    new = route_target(backends=(A, C))
    fresh = mon.configure([new])
    assert [key[0] for key in fresh] == [C]
    assert mon.status(new, A) == DOWN, "still down, no new grace period"
    assert mon.status(new, C) == UNKNOWN
    assert [key[0] for key in mon.unknown_keys()] == [C]
    assert mon.state((B, "/health", new.client_key)) is None, "the removed backend is forgotten"


def test_a_result_for_a_probe_that_no_longer_exists_is_ignored() -> None:
    target = route_target(backends=(A,))
    mon = monitor()
    (key,) = mon.configure([target])
    mon.configure([])
    assert mon.record_probe(key, False) == (UNKNOWN, UNKNOWN)


# -- selection and passive marks ----------------------------------------------------------------


def test_round_robin_over_a_pool() -> None:
    target = route_target(backends=(A, B, C))
    mon = monitor()
    mon.configure([target])
    assert [mon.select(target) for _ in range(6)] == [A, B, C, A, B, C]


def test_passive_mark_steers_selection_and_expires() -> None:
    clock = Clock()
    target = route_target(backends=(A, B))
    mon = monitor(clock=clock, unhealthy=30.0)
    mon.configure([target])
    mon.mark_passive(A)
    assert [mon.select(target) for _ in range(4)] == [B, B, B, B]
    assert mon.passive_remaining(A) == 30.0
    clock.now += 30.0
    assert sorted(mon.select(target) for _ in range(4)) == [A, A, B, B]
    assert mon.passive_remaining(A) == 0.0


def test_a_passive_mark_never_makes_a_model_unavailable() -> None:
    target = route_target(backends=(A, B))
    mon = monitor()
    mon.configure([target])
    mon.mark_passive(A)
    mon.mark_passive(B)
    assert mon.available(target)
    assert mon.candidates(target) == [A, B]
    assert {mon.select(target) for _ in range(4)} == {A, B}, "all marked: all are used"


def test_single_backend_with_a_passive_mark_is_still_used() -> None:
    target = route_target(backends=(A,))
    mon = monitor()
    mon.configure([target])
    mon.mark_passive(A)
    assert mon.select(target) == A


def test_retry_picks_a_different_backend_and_does_not_skew_the_rotation() -> None:
    target = route_target(backends=(A, B))
    mon = monitor()
    mon.configure([target])
    firsts = []
    for _ in range(4):
        first = mon.select(target)
        assert first is not None
        second = mon.select(target, exclude={first})
        assert second is not None and second != first
        firsts.append(first)
    assert firsts == [A, B, A, B], "first attempts keep alternating"
    assert mon.select(target, exclude={A, B}) is None


def test_down_backends_are_not_retried_on_even_when_unmarked() -> None:
    target = route_target(backends=(A, B))
    mon = monitor()
    mon.configure([target])
    probe(mon, target, B, False)
    assert mon.select(target, exclude={A}) is None


def test_unhealthy_seconds_zero_disables_passive_marks() -> None:
    target = route_target(backends=(A, B))
    mon = monitor(unhealthy=0.0)
    mon.configure([target])
    mon.mark_passive(A)
    assert [mon.select(target) for _ in range(2)] == [A, B]


def test_prune_passive_drops_expired_marks() -> None:
    clock = Clock()
    mon = monitor(clock=clock)
    mon.mark_passive(A)
    clock.now += 31
    mon.prune_passive()
    assert mon._passive_until == {}
