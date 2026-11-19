"""The fakes checked directly, without a router: if these fail, distrust everything else.

Written async on purpose: it keeps the asyncio plugin of the dev group in use
and shows the fakes do not depend on the caller's event loop.
"""

from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest

from tests.fakes import FakeKokoro, FakeReranker, FakeTEI, FakeVLLM, FakeWhisper, parse_multipart
from tests.harness.stack import user

CHAT = "/v1/chat/completions"
PAYLOAD = {"model": "acme/unit", "messages": user("hi there")}


@pytest.fixture
def vllm(loop_thread):
    fake = FakeVLLM(loop_thread, ["acme/unit"]).start()
    yield fake
    fake.close()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=5, trust_env=False) as http:
        yield http


async def wait_for(predicate, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_completion_is_deterministic_and_recorded(vllm, client):
    r = await client.post(vllm.url + CHAT, json=PAYLOAD, headers={"X-Probe": "1"})

    assert r.status_code == 200
    assert r.content == FakeVLLM.render_completion(PAYLOAD)
    seen = vllm.last(CHAT)
    assert seen.json == PAYLOAD and seen.headers["x-probe"] == "1"
    assert seen.sent == r.content and seen.completed and not seen.aborted
    assert r.json()["choices"][0]["message"]["reasoning"]


async def test_stream_has_usage_event_only_when_asked(vllm, client):
    plain = await client.post(vllm.url + CHAT, json={**PAYLOAD, "stream": True})
    asked = await client.post(
        vllm.url + CHAT, json={**PAYLOAD, "stream": True, "stream_options": {"include_usage": True}}
    )

    assert plain.headers["content-type"].startswith("text/event-stream")
    assert plain.content == b"".join(FakeVLLM.render_stream(PAYLOAD))
    assert b'"usage"' not in plain.content and b'"reasoning"' in plain.content
    assert asked.content == b"".join(FakeVLLM.render_stream(PAYLOAD, include_usage=True))
    assert b'"choices":[],"usage":' in asked.content
    assert asked.content.endswith(b"data: [DONE]\n\n")


async def test_models_and_health(vllm, client):
    models = (await client.get(vllm.url + "/v1/models")).json()
    assert models["data"][0]["id"] == "acme/unit" and models["data"][0]["max_model_len"] == 32768

    vllm.health_status = 503
    assert (await client.get(vllm.url + "/health")).status_code == 503
    vllm.reset()
    assert (await client.get(vllm.url + "/health")).status_code == 200


async def test_unknown_model_is_a_vllm_style_404(vllm, client):
    r = await client.post(vllm.url + CHAT, json={**PAYLOAD, "model": "nope"})

    assert r.status_code == 404
    assert r.json()["error"]["type"] == "NotFoundError"


async def test_forced_status_delay_and_times(vllm, client):
    raw = vllm.fail(503, {"error": {"message": "boom"}}, times=1)
    vllm.delay(0.2, times=1)
    started = asyncio.get_running_loop().time()

    first = await client.post(vllm.url + CHAT, json=PAYLOAD)
    second = await client.post(vllm.url + CHAT, json=PAYLOAD)

    assert (first.status_code, first.content) == (503, raw)
    assert asyncio.get_running_loop().time() - started >= 0.2
    assert second.status_code == 200  # both faults were used up
    assert (await client.get(vllm.url + "/health")).status_code == 200  # path=None spares /health


async def test_faults_queue_up_one_per_request(vllm, client):
    vllm.fail(400, times=1)
    vllm.fail(500, times=1)

    statuses = [(await client.post(vllm.url + CHAT, json=PAYLOAD)).status_code for _ in range(3)]

    assert statuses == [400, 500, 200]


async def test_caller_abort_is_recorded(vllm, client):
    vllm.delay(5.0)

    with pytest.raises(httpx.ReadTimeout):
        await client.post(vllm.url + CHAT, json=PAYLOAD, timeout=0.2)

    seen = vllm.last(CHAT)
    await wait_for(lambda: seen.aborted)
    assert not seen.completed


async def test_never_answering_and_abort(vllm, client):
    vllm.hang()

    with pytest.raises(httpx.ReadTimeout):
        await client.post(vllm.url + CHAT, json=PAYLOAD, timeout=0.2)

    await wait_for(lambda: vllm.last(CHAT).aborted)


async def test_stream_abort_is_recorded(vllm, client):
    vllm.stream_gap = 0.2

    async with client.stream("POST", vllm.url + CHAT, json={**PAYLOAD, "stream": True}) as response:
        async for _ in response.aiter_raw():
            break

    seen = vllm.last(CHAT)
    await wait_for(lambda: seen.aborted)
    assert not seen.completed and seen.sent


async def test_drop_mid_stream(vllm, client):
    vllm.drop(after=2)
    received = b""

    with pytest.raises(httpx.RemoteProtocolError):
        async with client.stream("POST", vllm.url + CHAT, json={**PAYLOAD, "stream": True}) as response:
            async for chunk in response.aiter_raw():
                received += chunk

    assert received == b"".join(FakeVLLM.render_stream(PAYLOAD)[:2])
    seen = vllm.last(CHAT)
    assert seen.dropped and not seen.aborted and not seen.completed


async def test_connection_reset_before_any_response(vllm, client):
    vllm.reset_connection()

    with pytest.raises(httpx.RemoteProtocolError):
        await client.post(vllm.url + CHAT, json=PAYLOAD)


async def test_stop_refuses_connections_and_start_reuses_the_port(vllm, client):
    port = vllm.port
    vllm.stop()

    with pytest.raises(httpx.ConnectError):
        await client.get(vllm.url + "/health")

    vllm.start()
    assert vllm.port == port
    assert (await client.get(vllm.url + "/health")).status_code == 200


async def test_tarpit_makes_connects_hang(vllm, client):
    vllm.tarpit()

    with pytest.raises(httpx.ConnectTimeout):
        await client.get(vllm.url + "/health", timeout=httpx.Timeout(5, connect=0.5))

    vllm.reset()
    assert (await client.get(vllm.url + "/health")).status_code == 200


async def test_pause_and_resume_stream(vllm, client):
    vllm.pause_stream_after(2)
    received = b""

    async with client.stream("POST", vllm.url + CHAT, json={**PAYLOAD, "stream": True}) as response:
        async for chunk in response.aiter_raw():
            received += chunk
            if received == b"".join(FakeVLLM.render_stream(PAYLOAD)[:2]):
                await asyncio.sleep(0.2)
                assert not vllm.last(CHAT).completed  # still held back
                vllm.resume_stream()

    assert received == b"".join(FakeVLLM.render_stream(PAYLOAD))


async def test_tei_embeddings(loop_thread, client):
    fake = FakeTEI(loop_thread).start()
    try:
        ok = await client.post(fake.url + "/v1/embeddings", json={"model": "m", "input": ["a b", "c"]})
        too_many = await client.post(fake.url + "/v1/embeddings", json={"model": "m", "input": ["x"] * 65})

        assert ok.json()["usage"] == {"prompt_tokens": 5, "total_tokens": 5}
        assert [d["embedding"] for d in ok.json()["data"]] == [FakeTEI.vector("a b"), FakeTEI.vector("c")]
        assert too_many.status_code == 422 and too_many.content == FakeTEI.too_many(65)
        assert (await client.get(fake.url + "/v1/models")).status_code == 404
    finally:
        fake.close()


async def test_reranker_honours_top_n(loop_thread, client):
    fake = FakeReranker(loop_thread).start()
    try:
        body = {"model": "m", "query": "q", "documents": ["a", "b", "c"]}
        everything = await client.post(fake.url + "/v1/rerank", json=body)
        top = await client.post(fake.url + "/v1/rerank", json={**body, "top_n": 1})
        null = await client.post(fake.url + "/v1/rerank", json={**body, "top_n": None})

        assert len(everything.json()["results"]) == 3 and everything.json()["usage"] == {"total_tokens": 6}
        assert top.json()["results"] == everything.json()["results"][:1]
        assert null.status_code == 500
    finally:
        fake.close()


async def test_whisper_echoes_the_form(loop_thread, client):
    fake = FakeWhisper(loop_thread).start()
    audio = bytes(range(256)) * 8 + b"\r\n--trap\r\n"
    try:
        r = await client.post(
            fake.url + "/v1/audio/transcriptions",
            data={"model": "m", "language": "de", "timestamp_granularities[]": ["word", "segment"]},
            files={"file": ("a.wav", audio, "audio/wav")},
        )

        fields = r.json()["fake"]["fields"]
        assert [f["name"] for f in fields] == ["model", "language", "timestamp_granularities[]",
                                               "timestamp_granularities[]", "file"]  # fmt: skip
        assert fields[-1]["sha256"] == hashlib.sha256(audio).hexdigest()
        assert fake.last().form_values("timestamp_granularities[]") == ["word", "segment"]
        assert (await client.get(fake.url + "/health")).status_code == 404
    finally:
        fake.close()


async def test_kokoro_speech_and_voices(loop_thread, client):
    fake = FakeKokoro(loop_thread).start()
    try:
        speech = await client.post(fake.url + "/v1/audio/speech", json={"model": "k", "input": "hello"})
        voices = await client.get(fake.url + "/v1/audio/voices")

        assert speech.headers["content-type"] == "audio/mpeg" and speech.content == FakeKokoro.audio("hello")
        assert voices.json() == {"voices": FakeKokoro.voices}
    finally:
        fake.close()


def test_multipart_parser_is_binary_safe():
    payload = b"\x00\xff\r\n--not-the-boundary\r\n" + bytes(range(256))
    body = (
        b'--B\r\nContent-Disposition: form-data; name="model"\r\n\r\nm\r\n'
        b'--B\r\nContent-Disposition: form-data; name="file"; filename="x.bin"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n" + payload + b"\r\n--B--\r\n"
    )

    fields = parse_multipart("multipart/form-data; boundary=B", body)

    assert [(f.name, f.value, f.filename) for f in fields] == [("model", "m", None), ("file", None, "x.bin")]
    assert fields[1].size == len(payload) and fields[1].sha256 == hashlib.sha256(payload).hexdigest()
