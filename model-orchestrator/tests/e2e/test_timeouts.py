"""Section 5 "Limits": read timeouts per endpoint, connect timeout, body size. C3, C17, C18, C1 (timeout)."""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from tests.fakes import FakeVLLM
from tests.harness import world as w
from tests.harness.stack import route, user
from tests.harness.util import error_of, sse_data, wait_until

CHAT = "/v1/chat/completions"
EMBED = "/v1/embeddings"
READ_TIMEOUT = 1  # CHAT_READ_TIMEOUT_S for the stacks of this module
BIG_MB = int(os.environ.get("E2E_BIG_BODY_MB", "32"))


class Slow:
    """A router with a 1 s chat read timeout: one single-backend chat route, a pool, embeddings."""

    def __init__(self, factory):
        self.solo = factory.fake("vllm", ["acme/slow"], name="solo")
        self.pool = [factory.fake("vllm", ["acme/pooled"], name=f"pool-{i}") for i in (1, 2)]
        self.embed = factory.fake("tei", name="embed")
        self.fakes = [self.solo, *self.pool, self.embed]
        self.stack = factory.stack(
            [
                route("Slow", "chat", self.solo, backend_model_name="acme/slow", models=["slow"]),
                route("Slow-Pool", "chat", self.pool, backend_model_name="acme/pooled", models=["slow-pool"]),
                route("Slow-Embed", "embedding", self.embed, models=["slow-embed"]),
            ],
            name="timeouts",
            env={"CHAT_READ_TIMEOUT_S": str(READ_TIMEOUT)},
        )


@pytest.fixture(scope="module")
def _slow(module_factory):
    return Slow(module_factory)


@pytest.fixture
def slow(_slow):
    for fake in _slow.fakes:
        fake.reset()
    yield _slow
    for fake in _slow.fakes:
        fake.reset()


def timed(fn, *args, **kwargs):
    started = time.monotonic()
    result = fn(*args, **kwargs)
    return result, time.monotonic() - started


# ---- read timeout: contract ---------------------------------------------------


def test_chat_read_timeout_comes_from_the_environment(slow):
    slow.solo.hang(path=CHAT)

    r, elapsed = timed(slow.stack.chat, "slow")

    assert r.status_code in (503, 504)  # 503 today, 504 after C3
    error_of(r)
    assert READ_TIMEOUT * 0.8 <= elapsed <= READ_TIMEOUT + 2.0
    seen = slow.solo.last(CHAT)
    wait_until(lambda: seen.aborted, timeout=2, what="the router to drop the timed-out backend call")


def test_a_backend_slower_than_usual_but_inside_the_timeout_succeeds(slow):
    slow.solo.delay(READ_TIMEOUT * 0.5, path=CHAT)

    r, elapsed = timed(slow.stack.chat, "slow")

    assert r.status_code == 200
    assert elapsed >= READ_TIMEOUT * 0.5


def test_the_chat_timeout_does_not_apply_to_embeddings(slow):
    """Embeddings have their own, fixed read timeout (60 s): 1.6 s is fine here."""
    slow.embed.delay(READ_TIMEOUT * 1.6, path=EMBED)

    r = slow.stack.post(EMBED, json={"model": "slow-embed", "input": "x"})

    assert r.status_code == 200


def test_the_timeout_is_per_read_not_for_the_whole_stream(slow):
    """A stream may run far longer than the read timeout as long as bytes keep coming."""
    slow.solo.stream_gap = 0.25  # 9 events: over 2 s in total, never 1 s of silence

    s = slow.stack.chat_stream("slow")

    assert s.error is None and s.status == 200
    assert s.elapsed > READ_TIMEOUT * 2
    assert s.raw == b"".join(FakeVLLM.render_stream({"model": "acme/slow", "messages": user("hello")}))


def test_stream_that_stalls_midway_ends_with_an_error_event(slow):
    slow.solo.pause_stream_after(3)
    sent = b"".join(FakeVLLM.render_stream({"model": "acme/slow", "messages": user("hello")})[:3])

    s = slow.stack.chat_stream("slow")

    assert s.status == 200 and s.error is None
    assert s.raw.startswith(sent)
    tail = sse_data(s.raw[len(sent) :])
    assert len(tail) == 2 and tail[1] == "[DONE]"
    assert tail[0]["error"]["message"]
    assert READ_TIMEOUT * 0.8 <= s.elapsed <= READ_TIMEOUT + 2.0


# ---- C3 -----------------------------------------------------------------------


@pytest.mark.change("C3")
def test_read_timeout_is_504_backend_timeout(slow):
    slow.solo.hang(path=CHAT)

    r = slow.stack.chat("slow")

    assert r.status_code == 504
    error = error_of(r)
    assert error.get("code") == "backend_timeout"
    assert error["type"] == "api_error"
    assert slow.solo.url not in error["message"]
    assert r.headers.get("x-router-error-source") == "router"


@pytest.mark.change("C3")
def test_read_timeout_is_not_retried_on_another_backend(slow):
    for fake in slow.pool:
        fake.hang(path=CHAT)

    r, elapsed = timed(slow.stack.chat, "slow-pool")

    assert r.status_code == 504
    assert sum(len(fake.calls(CHAT)) for fake in slow.pool) == 1
    assert elapsed < READ_TIMEOUT * 2 - 0.2  # one timeout, not two


@pytest.mark.legacy_only
def test_read_timeout_is_503_after_a_second_attempt_today(slow):
    for fake in slow.pool:
        fake.hang(path=CHAT)

    r, elapsed = timed(slow.stack.chat, "slow-pool")

    assert r.status_code == 503
    assert error_of(r)["message"].startswith("All backends unavailable: ReadTimeout")
    assert [len(fake.calls(CHAT)) for fake in slow.pool] == [1, 1]
    assert elapsed >= READ_TIMEOUT * 2 * 0.9


@pytest.mark.change("C1")
def test_stream_timeout_before_the_first_byte_is_a_json_504(slow):
    slow.solo.hang(path=CHAT)

    s = slow.stack.chat_stream("slow")

    assert s.status == 504
    assert s.headers["content-type"].startswith("application/json")
    assert s.json()["error"].get("code") == "backend_timeout"


@pytest.mark.legacy_only
def test_stream_timeout_before_the_first_byte_is_an_error_event_today(slow):
    slow.solo.hang(path=CHAT)

    s = slow.stack.chat_stream("slow")

    assert s.status == 200
    assert len(s.data) == 2 and s.data[1] == "[DONE]"
    assert s.data[0]["error"]["message"].startswith("Backend unavailable")


@pytest.mark.feature("route_timeout")
def test_route_timeout_s_overrides_the_endpoint_default(factory):
    """Design 7.2: ``timeout_s`` on a route replaces the endpoint's read timeout (60 s for embeddings)."""
    backend = factory.fake("tei")
    stack = factory.stack([route("Quick", "embedding", backend, models=["quick"], timeout_s=1)])
    backend.hang(path=EMBED)

    r, elapsed = timed(stack.post, EMBED, json={"model": "quick", "input": "x"})

    assert r.status_code == 504
    assert error_of(r).get("code") == "backend_timeout"
    assert 0.8 <= elapsed <= 3.0


@pytest.mark.timeout(120)
@pytest.mark.skipif(
    not os.environ.get("E2E_SLOW"), reason="waits for the fixed 60 s read timeout; set E2E_SLOW=1"
)
def test_embeddings_read_timeout_is_sixty_seconds(slow):
    slow.embed.hang(path=EMBED)

    r, elapsed = timed(slow.stack.post, EMBED, json={"model": "slow-embed", "input": "x"}, timeout=90)

    assert r.status_code in (503, 504)
    assert 58 <= elapsed <= 66


# ---- connect timeout: contract (chat) and C17 (everywhere) ---------------------


@pytest.fixture(scope="module")
def unreachable(module_factory):
    """Fire one chat and one embeddings request at backends whose port swallows connections.

    Both run at the same time and each takes about ten seconds, so they are
    made once per module and the tests only look at the outcome. The routes
    have no health probe: nothing may take them out of rotation first.
    """
    chat_backend = module_factory.fake("vllm", ["acme/far-chat"], name="far-chat")
    embed_backend = module_factory.fake("tei", name="far-embed")
    stack = module_factory.stack(
        [
            route("Far-Chat", "chat", chat_backend, models=["far-chat"], health_path=None),
            route("Far-Embed", "embedding", embed_backend, models=["far-embed"], health_path=None),
        ],
        name="unreachable",
    )
    chat_backend.tarpit()
    embed_backend.tarpit()

    def attempt(path, body):
        started = time.monotonic()
        try:
            response = stack.post(path, json=body, timeout=13.0)
        except httpx.TimeoutException as exc:
            return exc, time.monotonic() - started
        return response, time.monotonic() - started

    with ThreadPoolExecutor(2) as pool:
        chat = pool.submit(attempt, CHAT, {"model": "far-chat", "messages": user("hi")})
        embed = pool.submit(attempt, EMBED, {"model": "far-embed", "input": "hi"})
        outcome = {"chat": chat.result(), "embed": embed.result()}
    yield outcome
    # On legacy the embeddings call is still stuck in connect(); do not wait for a graceful exit.
    stack.router.stop(timeout=0.3)


@pytest.mark.timeout(60)
def test_chat_connect_timeout_is_ten_seconds(unreachable):
    response, elapsed = unreachable["chat"]

    assert isinstance(response, httpx.Response), f"no answer within 13 s: {response!r}"
    assert response.status_code == 503
    error_of(response)
    assert 9.0 <= elapsed <= 12.0


@pytest.mark.timeout(60)
@pytest.mark.change("C17")
def test_connect_timeout_is_ten_seconds_on_other_endpoints_too(unreachable):
    response, elapsed = unreachable["embed"]

    assert isinstance(response, httpx.Response), f"embeddings still waiting to connect after {elapsed:.1f} s"
    assert response.status_code == 503
    assert error_of(response).get("code") == "backend_unreachable"
    assert 9.0 <= elapsed <= 12.0


# ---- body size ------------------------------------------------------------------


def test_large_json_body_passes(stack, world):
    """Clients send images inline. The default cap is 200 MB; ``E2E_BIG_BODY_MB`` sets the size tried here."""
    blob = "A" * (BIG_MB * 1024 * 1024)
    content = [
        {"type": "text", "text": "big"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + blob}},
    ]

    r = stack.post(
        CHAT, json={"model": w.VISION_ALIAS, "messages": [{"role": "user", "content": content}]}, timeout=120
    )

    assert r.status_code == 200, r.text[:300]
    received = world.vision.last(CHAT).json["messages"][0]["content"][1]["image_url"]["url"]
    assert len(received) == len(blob) + len("data:image/png;base64,")
    assert (
        hashlib.sha256(received.encode()).digest()
        == hashlib.sha256(("data:image/png;base64," + blob).encode()).digest()
    )


def test_large_upload_passes(stack, world):
    audio = os.urandom(1024) * (BIG_MB * 1024)

    r = stack.post(
        "/v1/audio/transcriptions",
        data={"model": w.STT_ALIAS},
        files={"file": ("long.wav", audio, "audio/wav")},
        timeout=120,
    )

    assert r.status_code == 200, r.text[:300]
    received = world.whisper.last().form_file("file")
    assert received.size == len(audio)
    assert received.sha256 == hashlib.sha256(audio).hexdigest()


@pytest.fixture(scope="module")
def capped(module_factory):
    backend = module_factory.fake("vllm", ["acme/small"], name="small")
    whisper = module_factory.fake("whisper", name="small-stt")
    stack = module_factory.stack(
        [
            route("Small", "chat", backend, models=["small"], backend_model_name="acme/small"),
            route("Small-STT", "stt", whisper, models=["small-stt"], health_path=None),
        ],
        name="max-body",
        env={"ROUTER_MAX_BODY_MB": "1"},
    )
    return stack, backend, whisper


@pytest.mark.change("C18")
def test_json_body_over_the_cap_is_413(capped):
    stack, backend, _ = capped
    backend.clear_requests()

    r = stack.chat("small", "x" * (2 * 1024 * 1024))

    assert r.status_code == 413
    assert error_of(r)["type"] == "invalid_request_error"
    assert not backend.calls(CHAT)


@pytest.mark.change("C18")
def test_upload_over_the_cap_is_413(capped):
    stack, _, whisper = capped
    whisper.clear_requests()
    files = {"file": ("big.wav", b"\x00" * (2 * 1024 * 1024), "audio/wav")}

    r = stack.post("/v1/audio/transcriptions", data={"model": "small-stt"}, files=files)

    assert r.status_code == 413
    error_of(r)
    assert not whisper.calls("/v1/audio/transcriptions")


def test_body_under_the_cap_passes(capped):
    stack, backend, _ = capped

    r = stack.chat("small", "x" * (512 * 1024))

    assert r.status_code == 200
    assert len(backend.last(CHAT).json["messages"][0]["content"]) == 512 * 1024


@pytest.mark.legacy_only
def test_the_router_itself_has_no_body_cap_today(capped):
    stack, backend, _ = capped

    assert stack.chat("small", "x" * (2 * 1024 * 1024)).status_code == 200
