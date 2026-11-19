"""Usage accounting: ``usage_daily`` (contract, read by the admin UI) and ``usage_hourly`` (design 7.6)."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.fakes import FakeReranker, FakeTEI, FakeVLLM
from tests.harness import world as w
from tests.harness.stack import route, user
from tests.harness.util import holds_for, wait_until

CHAT = "/v1/chat/completions"
EMBED = "/v1/embeddings"
AUDIO = {"file": ("a.wav", b"RIFF0000", "audio/wav")}


def daily(stack, key, expected: dict, timeout: float = 5.0) -> None:
    """Wait until ``usage_daily`` for ``key`` reads ``{route: (requests, errors)}``."""
    wait_until(
        lambda: stack.db.usage_daily(key) == expected, timeout=timeout, what=f"usage_daily == {expected}"
    )


def hourly(stack, key, count: int, timeout: float = 6.0) -> list[dict]:
    """Wait until ``count`` requests are in ``usage_hourly`` for ``key`` and return the rows."""
    return wait_until(
        lambda: (rows := stack.db.usage_hourly(key)) and sum(r["requests"] for r in rows) == count and rows,
        timeout=timeout, what=f"{count} request(s) in usage_hourly",
    )  # fmt: skip


@pytest.fixture(scope="module")
def keys(_stack):
    made = [_stack.db.create_key(owner=f"owner-{i}", rpm=1000) for i in range(16)]
    limited = _stack.db.create_key(owner="limited", rpm=1)
    restricted = _stack.db.create_key(owner="restricted", allowed=[w.EMBED_ROUTE])
    _stack.sync_keys()
    return {"pool": iter(made), "limited": limited, "restricted": restricted}


@pytest.fixture
def key(keys):
    """A key nobody used yet, so its usage rows belong to this test alone."""
    return next(keys["pool"])


# ---- usage_daily: unchanged meaning -------------------------------------------


def test_usage_daily_counts_each_forwarded_request_under_its_route_name(stack, world, key):
    stack.chat(w.CHAT_ALIAS, key=key)
    stack.chat(w.CHAT_ALIAS_2, key=key)  # another alias of the same route
    stack.chat(w.CHAT_DISCOVERED, key=key)  # a discovered id of the same route
    stack.post(EMBED, key=key, json={"model": w.EMBED_ALIAS, "input": "x"})
    stack.post("/v1/rerank", key=key, json={"model": w.RERANK_ALIAS, "query": "q", "documents": ["a"]})
    stack.post("/v1/audio/transcriptions", key=key, data={"model": w.STT_ALIAS}, files=AUDIO)
    stack.post("/v1/audio/speech", key=key, json={"model": w.TTS_ALIAS, "input": "x"})

    daily(stack, key, {
        w.CHAT_ROUTE: (3, 0), w.EMBED_ROUTE: (1, 0), w.RERANK_ROUTE: (1, 0), w.STT_ROUTE: (1, 0), w.TTS_ROUTE: (1, 0),
    })  # fmt: skip
    rows = stack.db.execute("SELECT DISTINCT day, owner FROM usage_daily WHERE api_key_id = %s", (key.id,))
    assert rows == [{"day": stack.db.scalar("SELECT CURRENT_DATE"), "owner": key.owner}]


def test_usage_daily_counts_a_stream_once(stack, world, key):
    assert stack.chat_stream(w.CHAT_ALIAS, key=key).status == 200
    assert (
        stack.chat_stream(w.CHAT_ALIAS, key=key, body={"stream_options": {"include_usage": True}}).status
        == 200
    )

    daily(stack, key, {w.CHAT_ROUTE: (2, 0)})


def test_usage_daily_counts_backend_errors_as_errors(stack, world, key):
    world.chat.fail(400, times=1)
    stack.chat(w.CHAT_ALIAS, key=key)  # backend 4xx
    world.chat.fail(500, times=1)
    stack.chat(w.CHAT_ALIAS, key=key)  # backend 5xx
    stack.chat(w.CHAT_ALIAS, key=key)  # fine

    daily(stack, key, {w.CHAT_ROUTE: (3, 2)})


def test_usage_daily_counts_an_unreachable_backend_as_one_error(stack, world, key):
    world.kokoro.stop()  # no health probe on this route: the request is forwarded and fails

    assert (
        stack.post("/v1/audio/speech", key=key, json={"model": w.TTS_ALIAS, "input": "x"}).status_code == 503
    )

    daily(stack, key, {w.TTS_ROUTE: (1, 1)})


def test_usage_daily_ignores_requests_that_were_never_forwarded(stack, world, keys):
    limited, restricted = keys["limited"], keys["restricted"]
    assert stack.chat("no-such-model", key=limited).status_code == 404
    assert stack.chat(w.EMBED_ALIAS, key=limited).status_code == 400
    assert stack.chat(w.CHAT_ALIAS, key=restricted).status_code == 403
    assert stack.chat(w.CHAT_ALIAS, key=limited).status_code == 200
    assert stack.chat(w.CHAT_ALIAS, key=limited).status_code == 429

    daily(stack, limited, {w.CHAT_ROUTE: (1, 0)})
    holds_for(
        lambda: stack.db.usage_daily(limited) == {w.CHAT_ROUTE: (1, 0)}, seconds=1.0, what="usage_daily"
    )
    assert stack.db.usage_daily(restricted) == {}


def test_last_used_at_is_written(stack, world, key):
    assert stack.db.scalar("SELECT last_used_at FROM api_keys WHERE id = %s", (key.id,)) is None
    before = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=2)

    stack.chat(w.CHAT_ALIAS, key=key)

    stamp = wait_until(
        lambda: stack.db.scalar("SELECT last_used_at FROM api_keys WHERE id = %s", (key.id,)),
        timeout=5, what="last_used_at to be set",
    )  # fmt: skip
    assert before <= stamp <= dt.datetime.now(dt.UTC) + dt.timedelta(seconds=2)


def test_usage_history_survives_key_deletion(stack, world):
    key = stack.new_key(owner="leaver")
    stack.chat(w.CHAT_ALIAS, key=key)
    daily(stack, key, {w.CHAT_ROUTE: (1, 0)})

    stack.db.delete_key(key)
    wait_until(lambda: not stack.authenticates(key), timeout=5, what="the key to be gone")

    assert stack.db.usage_daily(key) == {w.CHAT_ROUTE: (1, 0)}


# ---- usage_hourly: tokens and outcomes ------------------------------------------

pytest_feature = pytest.mark.feature("usage_hourly")


def only(rows: list[dict]) -> dict:
    assert len(rows) == 1, rows
    return rows[0]


@pytest_feature
def test_hourly_row_for_a_chat_completion(stack, world, key):
    stack.chat(w.CHAT_ALIAS, "count these five words please", key=key)

    row = only(hourly(stack, key, 1))
    usage = FakeVLLM.usage({"messages": user("count these five words please")})
    assert (row["model"], row["route"], row["outcome"], row["owner"]) == (
        w.CHAT_ALIAS,
        w.CHAT_ROUTE,
        "ok",
        key.owner,
    )
    assert (row["requests"], row["prompt_tokens"], row["completion_tokens"]) == (
        1, usage["prompt_tokens"], usage["completion_tokens"],
    )  # fmt: skip
    assert row["duration_ms_sum"] >= 0
    now = dt.datetime.now(dt.UTC)
    assert row["hour"] == now.replace(minute=0, second=0, microsecond=0) or now.minute == 0


@pytest_feature
def test_hourly_model_is_the_id_the_client_sent(stack, world, key):
    stack.chat(w.CHAT_ALIAS, key=key)
    stack.chat(w.CHAT_ALIAS, key=key)
    stack.chat(w.CHAT_DISCOVERED, key=key)

    rows = {r["model"]: r for r in hourly(stack, key, 3)}
    assert set(rows) == {w.CHAT_ALIAS, w.CHAT_DISCOVERED}
    assert rows[w.CHAT_ALIAS]["requests"] == 2 and rows[w.CHAT_DISCOVERED]["requests"] == 1
    assert {r["route"] for r in rows.values()} == {w.CHAT_ROUTE}
    usage = FakeVLLM.usage({"messages": user("hello")})
    assert rows[w.CHAT_ALIAS]["prompt_tokens"] == 2 * usage["prompt_tokens"]
    assert rows[w.CHAT_ALIAS]["completion_tokens"] == 2 * usage["completion_tokens"]


@pytest_feature
def test_hourly_tokens_for_a_stream_the_client_did_not_ask_usage_for(stack, world, key):
    """The router asks the backend for usage itself and removes that one event again."""
    s = stack.chat_stream(w.CHAT_ALIAS, "stream tokens", key=key)

    seen = world.chat.last(CHAT)
    assert seen.json.get("stream_options") == {"include_usage": True}
    assert b'"usage"' in seen.sent  # the backend did send the usage-only event ...
    plain = {"model": w.CHAT_BACKEND_MODEL, "messages": user("stream tokens")}
    assert s.raw == b"".join(FakeVLLM.render_stream(plain))  # ... and the client got the stream without it
    assert b'"usage"' not in s.raw
    row = only(hourly(stack, key, 1))
    usage = FakeVLLM.usage(plain)
    assert (row["outcome"], row["prompt_tokens"], row["completion_tokens"]) == (
        "ok", usage["prompt_tokens"], usage["completion_tokens"],
    )  # fmt: skip


@pytest_feature
def test_hourly_tokens_for_a_stream_the_client_asked_usage_for(stack, world, key):
    s = stack.chat_stream(
        w.CHAT_ALIAS, "stream tokens", key=key, body={"stream_options": {"include_usage": True}}
    )

    assert s.raw == world.chat.last(CHAT).sent  # untouched, usage event included
    assert [d for d in s.data if isinstance(d, dict) and d.get("choices") == []]
    row = only(hourly(stack, key, 1))
    usage = FakeVLLM.usage({"messages": user("stream tokens")})
    assert (row["prompt_tokens"], row["completion_tokens"]) == (
        usage["prompt_tokens"],
        usage["completion_tokens"],
    )


@pytest_feature
def test_stream_options_the_client_sent_survive(stack, world, key):
    """Other stream options pass through, and a client that said ``include_usage: false`` gets no usage event."""
    s = stack.chat_stream(
        w.CHAT_ALIAS,
        key=key,
        body={"stream_options": {"include_usage": False, "continuous_usage_stats": False}},
    )

    assert s.status == 200
    assert b'"usage"' not in s.raw
    assert world.chat.last(CHAT).json["stream_options"]["continuous_usage_stats"] is False
    assert only(hourly(stack, key, 1))["outcome"] == "ok"


@pytest_feature
def test_hourly_tokens_for_embeddings_and_rerank(stack, world, key):
    stack.post(EMBED, key=key, json={"model": w.EMBED_ALIAS, "input": ["a b c", "d"]})
    stack.post(
        "/v1/rerank", key=key, json={"model": w.RERANK_ALIAS, "query": "q r", "documents": ["a", "b c"]}
    )

    rows = {r["route"]: r for r in hourly(stack, key, 2)}
    embed, rerank = rows[w.EMBED_ROUTE], rows[w.RERANK_ROUTE]
    assert embed["prompt_tokens"] == FakeTEI.tokens(["a b c", "d"]) and embed["completion_tokens"] == 0
    # rerank reports total_tokens only; it is all input
    assert rerank["prompt_tokens"] + rerank["completion_tokens"] == FakeReranker.tokens("q r", ["a", "b c"])


@pytest_feature
def test_hourly_counts_speech_requests_without_tokens(stack, world, key):
    stack.post("/v1/audio/transcriptions", key=key, data={"model": w.STT_ALIAS}, files=AUDIO)
    stack.post("/v1/audio/speech", key=key, json={"model": w.TTS_ALIAS, "input": "x"})

    rows = {r["route"]: r for r in hourly(stack, key, 2)}
    assert set(rows) == {w.STT_ROUTE, w.TTS_ROUTE}
    assert all(
        r["outcome"] == "ok" and r["prompt_tokens"] == 0 and r["completion_tokens"] == 0
        for r in rows.values()
    )


@pytest_feature
def test_hourly_outcomes(stack, world, keys):
    key = keys["limited"]  # rpm 1; other tests may have used it, so count from here
    base = {r["outcome"]: r["requests"] for r in stack.db.usage_hourly(key)}
    wait_until(
        lambda: stack.chat(w.CHAT_ALIAS, key=key).status_code == 429,
        timeout=5,
        what="the key to be rate limited",
    )
    other = next(keys["pool"])
    world.chat.fail(400, times=1)
    stack.chat(w.CHAT_ALIAS, key=other)  # client_error
    world.chat.fail(500, times=1)
    stack.chat(w.CHAT_ALIAS, key=other)  # backend_error
    world.chat.drop(after=2, times=1)
    stack.chat_stream(w.CHAT_ALIAS, key=other)  # midstream_error
    stack.chat(w.CHAT_ALIAS, key=other)  # ok

    outcomes = {r["outcome"]: r["requests"] for r in hourly(stack, other, 4)}
    assert outcomes == {"client_error": 1, "backend_error": 1, "midstream_error": 1, "ok": 1}
    wait_until(
        lambda: {r["outcome"]: r["requests"] for r in stack.db.usage_hourly(key)}.get("rate_limited", 0)
        > base.get("rate_limited", 0),
        timeout=6, what="the rate-limited request to be recorded",
    )  # fmt: skip
    # usage_daily keeps its old meaning next to it: forwarded requests only, three of four are errors
    daily(stack, other, {w.CHAT_ROUTE: (4, 3)})


@pytest_feature
def test_hourly_records_duration(stack, world, key):
    world.chat.delay(0.4, times=1)

    stack.chat(w.CHAT_ALIAS, key=key)

    assert 400 <= only(hourly(stack, key, 1))["duration_ms_sum"] < 5000


@pytest_feature
def test_response_without_usage_is_still_delivered(stack, world, key):
    """Token extraction is best effort: a body the router cannot read costs the client nothing."""
    world.chat.fail(200, "this is not json at all", content_type="application/json", times=1)

    r = stack.chat(w.CHAT_ALIAS, key=key)

    assert r.status_code == 200 and r.content == b"this is not json at all"
    row = only(hourly(stack, key, 1))
    assert (row["outcome"], row["prompt_tokens"], row["completion_tokens"]) == ("ok", 0, 0)


@pytest_feature
def test_usage_from_stream_false_leaves_the_request_alone(factory):
    backend = factory.fake("vllm", ["acme/strict"])
    stack = factory.stack(
        [
            route(
                "Strict",
                "chat",
                backend,
                models=["strict"],
                backend_model_name="acme/strict",
                usage_from_stream=False,
            )
        ]
    )

    s = stack.chat_stream("strict")

    assert s.status == 200
    assert "stream_options" not in backend.last(CHAT).json
    assert s.raw == backend.last(CHAT).sent
    row = only(hourly(stack, stack.key, 1))
    assert (row["prompt_tokens"], row["completion_tokens"]) == (0, 0)
