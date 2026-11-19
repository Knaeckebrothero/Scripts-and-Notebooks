"""The SSE splitter and usage extraction (design 7.6).

The splitter must be byte-exact for every way a stream can be cut into chunks,
so the tests below cut each sample at every position (and at every pair of
positions for the short ones).
"""

from __future__ import annotations

import itertools
import json

import pytest

from model_orchestrator.sse import (
    SSESplitter,
    StreamInspector,
    TokenUsage,
    event_data,
    usage_from_body,
    usage_from_object,
)


def chunk(payload: dict, end: bytes = b"\n\n") -> bytes:
    return b"data: " + json.dumps(payload).encode() + end


ROLE = chunk({"id": "c1", "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]})
REASONING = chunk({"id": "c1", "choices": [{"index": 0, "delta": {"reasoning": "let me think"}}]})
CONTENT = chunk({"id": "c1", "choices": [{"index": 0, "delta": {"content": 'Grüße 👋 \\n\\n "usage"'}}]})
FINISH = chunk({"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
USAGE_ONLY = chunk(
    {"id": "c1", "choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}
)
DONE = b"data: [DONE]\n\n"

EVENTS = [ROLE, REASONING, CONTENT, FINISH, USAGE_ONLY, DONE]
STREAM = b"".join(EVENTS)


def split_all(pieces: list[bytes]) -> tuple[list[bytes], bytes]:
    splitter = SSESplitter()
    events: list[bytes] = []
    for piece in pieces:
        events.extend(splitter.feed(piece))
    last, rest = splitter.finish()
    return events + last, rest


def cut(data: bytes, positions: tuple[int, ...]) -> list[bytes]:
    bounds = [0, *positions, len(data)]
    return [data[a:b] for a, b in itertools.pairwise(bounds)]


# -- every chunk boundary -----------------------------------------------------------


def test_whole_stream_in_one_chunk() -> None:
    assert split_all([STREAM]) == (EVENTS, b"")


def test_every_single_chunk_boundary() -> None:
    for position in range(len(STREAM) + 1):
        events, rest = split_all(cut(STREAM, (position,)))
        assert events == EVENTS, f"cut at {position}"
        assert rest == b""


def test_one_byte_at_a_time() -> None:
    events, rest = split_all([bytes([b]) for b in STREAM])
    assert events == EVENTS and rest == b""


@pytest.mark.parametrize(
    "terminator",
    [b"\n\n", b"\r\n\r\n", b"\r\r", b"\r\n\n", b"\n\r\n", b"\r\n\r", b"\n\r", b"\r\r\n"],
    ids=lambda t: t.decode().replace("\r", "CR").replace("\n", "LF"),
)
def test_every_pair_of_boundaries_for_every_terminator(terminator: bytes) -> None:
    """All two-cut chunkings of a short stream, for each legal blank-line spelling."""
    events = [
        b'data: {"a": 1}' + terminator,
        b": keep-alive comment" + terminator,
        b"event: x\ndata: one\ndata: two" + terminator,
        b"data: [DONE]" + terminator,
    ]
    stream = b"".join(events)
    for first, second in itertools.combinations_with_replacement(range(len(stream) + 1), 2):
        got, rest = split_all(cut(stream, (first, second)))
        assert b"".join(got) + rest == stream, f"not byte-exact for cuts {first},{second}"
        assert got == events, f"cuts {first},{second}"
        assert rest == b""


def test_crlf_inside_an_event_is_one_line_ending_not_a_blank_line() -> None:
    stream = b"event: x\r\ndata: 1\r\n\r\ndata: 2\r\n\r\n"
    for position in range(len(stream) + 1):
        events, rest = split_all(cut(stream, (position,)))
        assert events == [b"event: x\r\ndata: 1\r\n\r\n", b"data: 2\r\n\r\n"], position
        assert rest == b""


def test_terminator_ending_in_cr_waits_for_a_possible_lf() -> None:
    splitter = SSESplitter()
    assert splitter.feed(b"data: 1\r\n\r") == []  # could still become CR LF CR LF
    assert splitter.feed(b"\ndata: 2") == [b"data: 1\r\n\r\n"]
    assert splitter.finish() == ([], b"data: 2")


def test_terminator_ending_in_cr_is_complete_at_end_of_stream() -> None:
    splitter = SSESplitter()
    assert splitter.feed(b"data: 1\r\r") == []
    assert splitter.finish() == ([b"data: 1\r\r"], b"")


def test_unterminated_tail_is_returned_as_rest() -> None:
    stream = ROLE + b'data: {"partial": tr'
    for position in range(len(stream) + 1):
        events, rest = split_all(cut(stream, (position,)))
        assert events == [ROLE] and rest == b'data: {"partial": tr', position


def test_extra_blank_lines_stay_in_the_stream() -> None:
    stream = b"data: 1\n\n\n\ndata: 2\n\n\n"
    for position in range(len(stream) + 1):
        events, rest = split_all(cut(stream, (position,)))
        assert b"".join(events) + rest == stream, position


def test_empty_feed_and_empty_stream() -> None:
    splitter = SSESplitter()
    assert splitter.feed(b"") == []
    assert splitter.finish() == ([], b"")


def test_a_body_that_is_not_an_event_stream_falls_back_to_raw() -> None:
    splitter = SSESplitter(max_pending=64)
    out: list[bytes] = []
    body = b"data: 1\n\n" + b"x" * 200 + b"\n\nmore"
    for piece in cut(body, (9, 80, 150)):
        out.extend(splitter.feed(piece))
        out.append(splitter.drain())
    events, rest = splitter.finish()
    assert events == []
    assert b"".join(out) + rest == body
    assert splitter.raw


def test_scan_position_survives_many_small_feeds() -> None:
    """A long event arriving in tiny pieces is still found (and not rescanned from the start)."""
    event = b"data: " + b"y" * 5000 + b"\n\n"
    events, rest = split_all([event[i : i + 7] for i in range(0, len(event), 7)])
    assert events == [event] and rest == b""


# -- event data ----------------------------------------------------------------------


def test_event_data() -> None:
    assert event_data(b"data: hello\n\n") == "hello"
    assert event_data(b"data:hello\n\n") == "hello"
    assert event_data(b"event: x\r\nid: 1\r\ndata: a\r\ndata: b\r\n\r\n") == "a\nb"
    assert event_data(b": comment\n\n") is None
    assert event_data(b"data: \xff\xfe\n\n") is None
    assert event_data(DONE) == "[DONE]"


# -- usage extraction ------------------------------------------------------------------


def test_usage_from_chat_object() -> None:
    obj = {"usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}
    assert usage_from_object(obj) == TokenUsage(11, 7)


def test_usage_from_embeddings_and_rerank_objects() -> None:
    assert usage_from_object({"usage": {"prompt_tokens": 5, "total_tokens": 5}}) == TokenUsage(5, 0)
    assert usage_from_object({"usage": {"total_tokens": 9}}) == TokenUsage(9, 0)
    assert usage_from_object({"usage": {"completion_tokens": 4, "total_tokens": 10}}) == TokenUsage(6, 4)


@pytest.mark.parametrize(
    "obj",
    [
        None,
        [],
        {},
        {"usage": None},
        {"usage": []},
        {"usage": {}},
        {"usage": {"prompt_tokens": "11"}},
        {"usage": {"prompt_tokens": -1}},
        {"usage": {"prompt_tokens": True}},
        {"usage": {"prompt_tokens": 1.5}},
    ],
)
def test_usage_that_cannot_be_read_is_none(obj: object) -> None:
    assert usage_from_object(obj) is None


def test_usage_from_body_small() -> None:
    body = json.dumps({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}).encode()
    assert usage_from_body(body) == TokenUsage(3, 2)
    assert usage_from_body(b"") is None
    assert usage_from_body(b'not json but "usage"') is None
    assert usage_from_body(b'{"text": "no counts here"}') is None
    assert usage_from_body(b'[{"usage": {"prompt_tokens": 1}}]') is None


def test_usage_from_body_large_is_found_without_parsing_everything() -> None:
    vectors = [{"object": "embedding", "index": i, "embedding": [0.123456] * 2000} for i in range(60)]
    tail = {
        "object": "list",
        "data": vectors,
        "model": "m",
        "usage": {"prompt_tokens": 480, "total_tokens": 480},
    }
    body = json.dumps(tail).encode()
    assert len(body) > 1024 * 1024
    assert usage_from_body(body) == TokenUsage(480, 0)
    head = {"usage": {"prompt_tokens": 7, "total_tokens": 7}, "object": "list", "data": vectors}
    assert usage_from_body(json.dumps(head).encode()) == TokenUsage(7, 0)
    assert usage_from_body(json.dumps({"object": "list", "data": vectors}).encode()) is None


# -- the stream inspector -----------------------------------------------------------------


def relay(pieces: list[bytes], *, strip: bool) -> tuple[bytes, StreamInspector]:
    """What the relay sends to the client for a stream cut into ``pieces``."""
    splitter = SSESplitter()
    inspector = StreamInspector(strip_usage_only=strip)
    out: list[bytes] = []
    for piece in pieces:
        out.extend(event for event in splitter.feed(piece) if inspector.keep(event))
    events, rest = splitter.finish()
    out.extend(event for event in events if inspector.keep(event))
    out.append(rest)
    return b"".join(out), inspector


def test_usage_only_event_is_removed_for_every_chunk_boundary() -> None:
    """The router asked for usage, the client did not: the client's stream is the
    backend's stream minus exactly that one event, however the bytes arrive."""
    expected = ROLE + REASONING + CONTENT + FINISH + DONE
    for position in range(len(STREAM) + 1):
        sent, inspector = relay(cut(STREAM, (position,)), strip=True)
        assert sent == expected, f"cut at {position}"
        assert inspector.usage == TokenUsage(11, 7)
        assert not inspector.error_seen


def test_stream_is_untouched_when_the_client_asked_for_usage() -> None:
    for position in range(0, len(STREAM) + 1, 3):
        sent, inspector = relay(cut(STREAM, (position,)), strip=False)
        assert sent == STREAM
        assert inspector.usage == TokenUsage(11, 7)


def test_only_the_usage_only_event_is_ever_dropped() -> None:
    inspector = StreamInspector(strip_usage_only=True)
    with_choices = chunk(
        {
            "choices": [{"index": 0, "delta": {"content": "x"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    )
    null_usage = chunk({"choices": [], "usage": None})
    assert inspector.keep(with_choices)  # usage rides on a content chunk: relayed, and read
    assert inspector.usage == TokenUsage(1, 1)
    assert inspector.keep(null_usage)
    assert inspector.keep(CONTENT)  # mentions "usage" inside a string
    assert inspector.keep(DONE)
    assert inspector.keep(b': "usage" in a comment\n\n')
    assert inspector.keep(b'data: {"usage": {broken\n\n')
    assert inspector.keep(b'data: [1, "usage"]\n\n')
    assert not inspector.keep(USAGE_ONLY)
    assert not inspector.keep(
        chunk({"usage": {"prompt_tokens": 2, "completion_tokens": 0}})
    )  # no choices key
    assert not inspector.keep(chunk({"choices": [], "usage": {"prompt_tokens": 1}}, end=b"\r\n\r\n"))


def test_the_done_marker_is_noticed() -> None:
    inspector = StreamInspector(strip_usage_only=True)
    assert inspector.keep(CONTENT) and inspector.keep(USAGE_ONLY) is False
    assert not inspector.done_seen
    mention = chunk({"choices": [{"index": 0, "delta": {"content": "write [DONE] when finished"}}]})
    assert inspector.keep(mention) and not inspector.done_seen, (
        "[DONE] inside a JSON string is not the marker"
    )
    assert inspector.keep(DONE) and inspector.done_seen
    for spelling in (b"data:[DONE]\n\n", b"data: [DONE]\r\n\r\n", b": note\ndata: [DONE]\n\n"):
        other = StreamInspector(strip_usage_only=False)
        assert other.keep(spelling) and other.done_seen, spelling


def test_error_event_is_noticed_and_relayed() -> None:
    inspector = StreamInspector(strip_usage_only=True)
    error = chunk({"error": {"message": "CUDA out of memory", "type": "api_error"}})
    assert inspector.keep(error)
    assert inspector.error_seen
    quiet = StreamInspector(strip_usage_only=True)
    assert quiet.keep(chunk({"choices": [{"delta": {"content": 'an "error" in text'}}]}))
    assert quiet.keep(chunk({"choices": [], "error": None}))
    assert not quiet.error_seen
