"""Section 5: speech-to-text (multipart), text-to-speech, voices. C6 and C15."""

from __future__ import annotations

import hashlib

import pytest

from tests.fakes import FakeKokoro, FakeWhisper
from tests.harness import world as w
from tests.harness.stack import route
from tests.harness.util import error_of

STT = "/v1/audio/transcriptions"
TTS = "/v1/audio/speech"
VOICES = "/v1/audio/voices"

# Not real audio, but every byte value and a few multipart-looking traps.
AUDIO = b"RIFF\x00\x00\x00\x00WAVE" + bytes(range(256)) * 64 + b"\r\n--boundary--\r\n\x00\xff"


def upload(name: str = "clip one.wav", content: bytes = AUDIO, ctype: str = "audio/wav"):
    return {"file": (name, content, ctype)}


# ---- speech-to-text --------------------------------------------------------


def test_transcription_round_trip(stack, world):
    r = stack.post(STT, data={"model": w.STT_ALIAS}, files=upload())

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    seen = world.whisper.last(STT)
    assert seen.headers["content-type"].startswith("multipart/form-data")
    assert r.content == seen.sent
    assert r.json()["text"] == FakeWhisper.transcript("clip one.wav", len(AUDIO))


def test_transcription_file_arrives_intact(stack, world):
    stack.post(STT, data={"model": w.STT_ALIAS}, files=upload("ünï code.mp3", AUDIO, "audio/mpeg"))

    received = world.whisper.last(STT).form_file("file")
    assert received is not None
    assert received.size == len(AUDIO)
    assert received.sha256 == hashlib.sha256(AUDIO).hexdigest()
    assert received.content_type == "audio/mpeg"
    assert received.filename in ("ünï code.mp3", "%C3%BCn%C3%AF%20code.mp3", "%C3%BCn%C3%AF code.mp3")


@pytest.mark.parametrize("alias", [w.STT_ALIAS, w.STT_ALIAS_2])
def test_transcription_model_is_rewritten_to_the_backend_name(stack, world, alias):
    stack.post(STT, data={"model": alias}, files=upload())

    assert world.whisper.last(STT).form_values("model") == [w.STT_BACKEND_MODEL]


def test_transcription_field_order_does_not_matter(stack, world):
    """Clients send ``file`` before or after ``model``; both work."""
    boundary = "e2eboundary"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + AUDIO + (
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{w.STT_ALIAS}\r\n--{boundary}--\r\n'
    ).encode()  # fmt: skip

    r = stack.post(STT, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})

    assert r.status_code == 200, r.text
    assert world.whisper.last(STT).form_file("file").sha256 == hashlib.sha256(AUDIO).hexdigest()


def test_transcription_wrong_model_type_is_400(stack, world):
    r = stack.post(STT, data={"model": w.CHAT_ALIAS}, files=upload())

    assert r.status_code == 400
    assert error_of(r)["type"] == "invalid_request_error"
    assert not world.whisper.calls(STT)


def test_transcription_unknown_model_is_404(stack, world):
    r = stack.post(STT, data={"model": "no-such-stt"}, files=upload())

    assert r.status_code == 404
    error_of(r)


def test_transcription_backend_error_keeps_its_status(stack, world):
    world.whisper.fail(415, {"detail": "unsupported audio format"})

    r = stack.post(STT, data={"model": w.STT_ALIAS}, files=upload())

    assert r.status_code == 415
    assert "unsupported audio format" in error_of(r)["message"]


@pytest.mark.change("C6")
def test_transcription_forwards_every_form_field(stack, world):
    fields = {
        "model": w.STT_ALIAS,
        "language": "de",
        "prompt": "Grüße, 世界",
        "temperature": "0.2",
        "response_format": "verbose_json",
        "timestamp_granularities[]": ["word", "segment"],  # a repeated field
        "x_unknown": "kept",
    }

    r = stack.post(STT, data=fields, files=upload())

    assert r.status_code == 200, r.text
    seen = world.whisper.last(STT)
    assert seen.form_values("model") == [w.STT_BACKEND_MODEL]
    assert seen.form_values("language") == ["de"]
    assert seen.form_values("prompt") == ["Grüße, 世界"]
    assert seen.form_values("temperature") == ["0.2"]
    assert seen.form_values("timestamp_granularities[]") == ["word", "segment"]
    assert seen.form_values("x_unknown") == ["kept"]
    assert seen.form_file("file").sha256 == hashlib.sha256(AUDIO).hexdigest()
    assert r.json()["language"] == "de"


@pytest.mark.change("C6")
def test_transcription_response_format_text_reaches_the_backend(stack, world):
    """Visible to the client: with the field forwarded the backend answers plain text."""
    r = stack.post(STT, data={"model": w.STT_ALIAS, "response_format": "text"}, files=upload())

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == FakeWhisper.transcript("clip one.wav", len(AUDIO))


@pytest.mark.legacy_only
def test_transcription_forwards_only_file_and_model_today(stack, world):
    fields = {"model": w.STT_ALIAS, "language": "de", "response_format": "text"}

    r = stack.post(STT, data=fields, files=upload())

    assert r.status_code == 200
    assert [f.name for f in world.whisper.last(STT).form] == ["model", "file"]
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.change("C6")
@pytest.mark.parametrize("how", ["no-file-part", "file-is-a-text-field", "not-multipart"])
def test_transcription_without_a_file_is_400(stack, world, how):
    if how == "no-file-part":
        r = stack.post(STT, data={"model": w.STT_ALIAS}, files={"other": ("a.txt", b"x", "text/plain")})
    elif how == "file-is-a-text-field":
        r = stack.post(STT, files={"model": (None, w.STT_ALIAS), "file": (None, "just text")})
    else:
        r = stack.post(STT, data={"model": w.STT_ALIAS})

    assert r.status_code == 400
    error_of(r)
    assert r.headers.get("x-request-id")
    assert not world.whisper.calls(STT)


@pytest.mark.legacy_only
def test_transcription_without_a_file_is_a_plain_500_today(stack, world):
    r = stack.post(STT, data={"model": w.STT_ALIAS}, files={"other": ("a.txt", b"x", "text/plain")})

    assert r.status_code == 500
    assert r.text == "Internal Server Error"
    assert "x-request-id" not in r.headers


@pytest.mark.change("C6")
def test_transcription_without_model_uses_the_first_stt_route(stack, world):
    r = stack.post(STT, files=upload())

    assert r.status_code == 200, r.text
    assert world.whisper.last(STT).form_values("model") == [w.STT_BACKEND_MODEL]


@pytest.mark.legacy_only
def test_transcription_without_model_defaults_to_a_hard_coded_name_today(stack, world):
    r = stack.post(STT, files=upload())

    assert r.status_code == 404
    assert error_of(r)["message"] == "Model 'whisper-large-v3-turbo' not found"


# ---- text-to-speech --------------------------------------------------------


def test_speech_returns_the_backends_bytes(stack, world):
    sent = {
        "model": w.TTS_ALIAS,
        "input": "Hello there ✓",
        "voice": "af_heart",
        "speed": 1.1,
        "x_unknown": [1],
    }

    r = stack.post(TTS, json=sent)

    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == FakeKokoro.audio("Hello there ✓")
    assert world.kokoro.last(TTS).json == {**sent, "model": w.TTS_BACKEND_MODEL}


def test_speech_relays_the_backends_content_type(stack, world):
    r = stack.post(TTS, json={"model": w.TTS_ALIAS_2, "input": "wav please", "response_format": "wav"})

    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == FakeKokoro.audio("wav please")


def test_speech_wrong_model_type_is_400(stack, world):
    r = stack.post(TTS, json={"model": w.STT_ALIAS, "input": "x"})

    assert r.status_code == 400
    error_of(r)


# ---- voices ----------------------------------------------------------------


def test_voices_needs_no_key_and_relays_the_backend_list(stack, world):
    r = stack.get(VOICES, key=None)

    assert r.status_code == 200, r.text
    assert r.json() == {"voices": FakeKokoro.voices}
    assert not [h for h in r.headers if h.lower().startswith("x-ratelimit")]


@pytest.mark.parametrize("alias", [w.TTS_ALIAS, w.TTS_ALIAS_2])
def test_voices_for_a_named_model(stack, world, alias):
    r = stack.get(VOICES, key=None, params={"model": alias})

    assert r.status_code == 200
    assert r.json() == {"voices": FakeKokoro.voices}


def test_voices_for_a_non_tts_model_is_400(stack, world):
    r = stack.get(VOICES, key=None, params={"model": w.CHAT_ALIAS})

    assert r.status_code == 400
    error_of(r)


def test_voices_for_an_unknown_model_is_404(stack, world):
    r = stack.get(VOICES, key=None, params={"model": "no-such-voice-model"})

    assert r.status_code == 404
    error_of(r)


def test_voices_without_any_tts_route_is_404(factory):
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack([route("Solo", "chat", backend, models=["solo"])])

    r = stack.get(VOICES, key=None)

    assert r.status_code == 404
    error_of(r)


def test_voices_picks_the_route_named_by_model(factory):
    first, second = factory.fake("kokoro", name="first"), factory.fake("kokoro", name="second")
    second.voices = ["zz_other"]
    stack = factory.stack(
        [
            route("TTS-One", "tts", first, models=["tts-one"], health_path=None),
            route("TTS-Two", "tts", second, models=["tts-two"], health_path=None),
        ]
    )

    assert stack.get(VOICES, key=None).json() == {"voices": FakeKokoro.voices}  # first tts route
    assert stack.get(VOICES, key=None, params={"model": "tts-two"}).json() == {"voices": ["zz_other"]}


@pytest.mark.change("C15")
def test_voices_are_served_from_a_cache(stack, world):
    answers = [stack.get(VOICES, key=None, params={"model": w.TTS_ALIAS}) for _ in range(4)]

    assert all(a.status_code == 200 and a.json() == {"voices": FakeKokoro.voices} for a in answers)
    assert len(world.kokoro.calls(VOICES)) <= 1  # 0 if an earlier test already filled the cache


@pytest.mark.legacy_only
def test_voices_call_the_backend_every_time_today(stack, world):
    for _ in range(4):
        assert stack.get(VOICES, key=None).status_code == 200

    assert len(world.kokoro.calls(VOICES)) == 4
