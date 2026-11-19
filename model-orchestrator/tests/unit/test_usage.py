"""Usage accounting: aggregation rules and the background writer (design 7.6)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest

from model_orchestrator import usage
from model_orchestrator.metrics import Metrics
from model_orchestrator.usage import UsageBatch, UsageEvent, UsageWriter, aggregate

AT = datetime(2026, 11, 18, 14, 37, 12, tzinfo=UTC)
HOUR = datetime(2026, 11, 18, 14, 0, tzinfo=UTC)


def event(outcome: str = usage.OK, **overrides: object) -> UsageEvent:
    base: dict[str, object] = {
        "at": AT,
        "key_id": 1,
        "owner": "alice",
        "model": "gpt-4o-mini",
        "route": "Gemma-Pool",
        "outcome": outcome,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "duration_ms": 0,
    }
    base.update(overrides)
    return UsageEvent(**base)  # type: ignore[arg-type]


def test_outcome_sets() -> None:
    assert usage.OUTCOMES == {
        "ok",
        "client_error",
        "backend_error",
        "unreachable",
        "timeout",
        "midstream_error",
        "client_closed",
        "unavailable",
        "rate_limited",
        "concurrency_limited",
    }
    assert usage.OUTCOMES - usage.FORWARDED_OUTCOMES == {"unavailable", "rate_limited", "concurrency_limited"}


# -- usage_daily keeps its exact meaning ---------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "requests", "errors"),
    [
        ("ok", 1, 0),
        ("client_closed", 1, 0),
        ("client_error", 1, 1),
        ("backend_error", 1, 1),
        ("unreachable", 1, 1),
        ("timeout", 1, 1),
        ("midstream_error", 1, 1),
    ],
)
def test_daily_counts_every_forwarded_request(outcome: str, requests: int, errors: int) -> None:
    batch = aggregate([event(outcome)])
    row = batch.daily[(1, "Gemma-Pool")]
    assert (row.requests, row.errors, row.owner) == (requests, errors, "alice")
    assert batch.forwarded_key_ids == {1}


@pytest.mark.parametrize("outcome", ["unavailable", "rate_limited", "concurrency_limited"])
def test_daily_ignores_requests_that_were_not_forwarded(outcome: str) -> None:
    batch = aggregate([event(outcome)])
    assert batch.daily == {}
    assert batch.forwarded_key_ids == set()
    assert batch.hourly[(HOUR, 1, "gpt-4o-mini", "Gemma-Pool", outcome)].requests == 1


def test_daily_is_keyed_by_route_name_not_model_id() -> None:
    batch = aggregate(
        [event(model="gpt-4o-mini"), event(model="gemma-4-moe"), event(model="x", route="Other")]
    )
    assert {key: row.requests for key, row in batch.daily.items()} == {(1, "Gemma-Pool"): 2, (1, "Other"): 1}


# -- usage_hourly records everything -----------------------------------------------------------


def test_hourly_is_keyed_by_hour_key_model_route_outcome_and_sums() -> None:
    batch = aggregate(
        [
            event(prompt_tokens=10, completion_tokens=5, duration_ms=100),
            event(prompt_tokens=1, completion_tokens=2, duration_ms=50),
            event("timeout", duration_ms=600_000),
            event(model="gemma-4-moe", prompt_tokens=7),
            event(key_id=2, owner="bob"),
            event(at=AT + timedelta(hours=1)),
        ]
    )
    ok = batch.hourly[(HOUR, 1, "gpt-4o-mini", "Gemma-Pool", "ok")]
    assert (ok.requests, ok.prompt_tokens, ok.completion_tokens, ok.duration_ms, ok.owner) == (
        2,
        11,
        7,
        150,
        "alice",
    )
    assert batch.hourly[(HOUR, 1, "gpt-4o-mini", "Gemma-Pool", "timeout")].duration_ms == 600_000
    assert batch.hourly[(HOUR, 1, "gemma-4-moe", "Gemma-Pool", "ok")].prompt_tokens == 7
    assert batch.hourly[(HOUR, 2, "gpt-4o-mini", "Gemma-Pool", "ok")].owner == "bob"
    assert (HOUR + timedelta(hours=1), 1, "gpt-4o-mini", "Gemma-Pool", "ok") in batch.hourly
    assert len(batch.hourly) == 5
    assert batch.events == 6


def test_hour_is_the_utc_hour_whatever_the_events_timezone() -> None:
    local = datetime(2026, 11, 18, 16, 7, tzinfo=timezone(timedelta(hours=1, minutes=30)))
    (key,) = aggregate([event(at=local)]).hourly
    assert key[0] == datetime(2026, 11, 18, 14, 0, tzinfo=UTC)


def test_the_latest_owner_wins() -> None:
    batch = aggregate([event(owner="old"), event(owner="new")])
    assert batch.daily[(1, "Gemma-Pool")].owner == "new"
    assert batch.hourly[(HOUR, 1, "gpt-4o-mini", "Gemma-Pool", "ok")].owner == "new"


# -- the writer --------------------------------------------------------------------------------


class Sink:
    """Stands in for the database."""

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.batches: list[tuple[UsageBatch, list[int]]] = []

    async def write(self, batch: UsageBatch, touch: list[int]) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("database is down")
        self.batches.append((batch, touch))

    def written(self) -> int:
        return sum(batch.events for batch, _ in self.batches)


async def run_until_idle(writer: UsageWriter, sink: Sink, *, expect_calls: int) -> asyncio.Task[None]:
    task = asyncio.create_task(writer.run())
    for _ in range(500):
        if sink.calls >= expect_calls and writer.depth() == 0:
            break
        await asyncio.sleep(0.005)
    return task


async def test_events_are_batched_and_written() -> None:
    sink = Sink()
    writer = UsageWriter(sink.write)
    for _ in range(3):
        assert writer.record(event())
    task = await run_until_idle(writer, sink, expect_calls=1)
    assert sink.written() == 3
    assert len(sink.batches) == 1, "what is queued at wake-up goes out as one batch"
    await writer.stop(task)
    assert task.done()


async def test_a_failed_batch_is_retried_with_backoff_then_written_once() -> None:
    sink = Sink(fail_times=2)
    metrics = Metrics(version="t", git_sha="t")
    writer = UsageWriter(sink.write, metrics=metrics, retry_delays_s=(0.01, 0.01, 0.01))
    writer.record(event())
    writer.record(event("timeout"))
    task = await run_until_idle(writer, sink, expect_calls=3)
    assert sink.calls == 3
    assert sink.written() == 2
    assert writer.dropped == 0
    assert metrics.usage_write_failures._value.get() == 2
    await writer.stop(task)


async def test_a_batch_is_dropped_after_the_last_retry_and_counted() -> None:
    sink = Sink(fail_times=99)
    metrics = Metrics(version="t", git_sha="t")
    writer = UsageWriter(sink.write, metrics=metrics, retry_delays_s=(0.01, 0.01))
    writer.record(event())
    writer.record(event())
    task = await run_until_idle(writer, sink, expect_calls=3)
    assert sink.calls == 3, "one attempt plus two retries"
    assert writer.dropped == 2
    assert metrics.usage_dropped._value.get() == 2
    assert sink.written() == 0
    # The writer survives and keeps going.
    sink.fail_times = 0
    writer.record(event())
    for _ in range(200):
        if sink.written():
            break
        await asyncio.sleep(0.005)
    assert sink.written() == 1
    await writer.stop(task)


async def test_a_full_queue_drops_new_records_without_blocking() -> None:
    metrics = Metrics(version="t", git_sha="t")
    writer = UsageWriter(Sink().write, metrics=metrics, max_queue=2)
    assert writer.record(event())
    assert writer.record(event())
    assert not writer.record(event())
    assert writer.depth() == 2 and writer.dropped == 1
    assert metrics.usage_dropped._value.get() == 1


async def test_stop_flushes_what_is_queued() -> None:
    sink = Sink()
    writer = UsageWriter(sink.write)
    task = asyncio.create_task(writer.run())
    await asyncio.sleep(0)
    for _ in range(5):
        writer.record(event())
    await writer.stop(task)
    assert task.done() and sink.written() == 5


async def test_stop_does_not_wait_for_retries() -> None:
    sink = Sink(fail_times=99)
    writer = UsageWriter(sink.write, retry_delays_s=(30.0,))
    task = asyncio.create_task(writer.run())
    writer._stopping = True  # as stop() sets it before the batch is attempted
    writer.record(event())
    await asyncio.wait_for(writer.stop(task), timeout=2)
    assert sink.calls == 1 and writer.dropped == 1


async def test_large_queues_are_written_in_bounded_batches() -> None:
    sink = Sink()
    writer = UsageWriter(sink.write, max_batch=10)
    for index in range(25):
        writer.record(event(key_id=index))
    task = await run_until_idle(writer, sink, expect_calls=3)
    assert [batch.events for batch, _ in sink.batches] == [10, 10, 5]
    await writer.stop(task)


async def test_last_used_is_touched_at_most_once_a_minute_and_only_for_forwarded_requests() -> None:
    sink = Sink()
    writer = UsageWriter(sink.write)
    task = asyncio.create_task(writer.run())
    writer.record(event(key_id=1))
    writer.record(event("rate_limited", key_id=2))
    await run_and_wait(sink, 1)
    writer.record(event(key_id=1))
    writer.record(event(key_id=3))
    await run_and_wait(sink, 2)
    assert sink.batches[0][1] == [1]
    assert sink.batches[1][1] == [3]
    await writer.stop(task)


async def run_and_wait(sink: Sink, batches: int) -> None:
    for _ in range(400):
        if len(sink.batches) >= batches:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"expected {batches} batch(es), got {len(sink.batches)}")
