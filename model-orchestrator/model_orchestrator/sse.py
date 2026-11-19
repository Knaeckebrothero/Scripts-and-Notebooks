"""Server-sent events: splitting a byte stream into whole events, and reading usage.

The splitter never changes a byte. Feeding it a stream in any chunking and
joining what it returns gives back the stream exactly; the only thing the relay
may do with the result is leave out one whole event.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# An event ends at a blank line: two line endings in a row, where a line ending
# is CRLF, LF or CR. CR followed by LF is always one line ending, never two, so
# the alternatives are spelled out instead of written as (EOL)(EOL).
_EVENT_END = re.compile(rb"\r\n(?:\r\n|\r|\n)|\r(?:\r\n|\r)|\n(?:\r\n|\r|\n)")
_LINE_END = re.compile(r"\r\n|\r|\n")
_CR = 0x0D

# A chat event is a few hundred bytes. If this much arrives without a blank line
# the body is not an event stream, and the splitter stops holding bytes back.
DEFAULT_MAX_PENDING = 4 * 1024 * 1024


class SSESplitter:
    """Turns arbitrary byte chunks into whole events, byte-exact.

    ``feed`` returns the events completed by a chunk, each including its
    terminator. ``finish`` returns what is left when the stream ends. In raw mode
    (after an oversized run of bytes with no terminator) ``feed`` returns nothing
    and ``drain`` hands the bytes over unsplit.
    """

    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING) -> None:
        self._buf = bytearray()
        self._scan = 0
        self._max_pending = max_pending
        self.raw = False

    @property
    def pending(self) -> int:
        return len(self._buf)

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self._buf += data
        if self.raw:
            return []
        events = self._split(final=False)
        if len(self._buf) > self._max_pending:
            self.raw = True
        return events

    def drain(self) -> bytes:
        """In raw mode: the bytes received since the last call, unsplit."""
        if not self.raw or not self._buf:
            return b""
        out = bytes(self._buf)
        self._buf.clear()
        self._scan = 0
        return out

    def finish(self) -> tuple[list[bytes], bytes]:
        """End of stream: the last complete events, then any unterminated rest."""
        events = [] if self.raw else self._split(final=True)
        rest = bytes(self._buf)
        self._buf.clear()
        self._scan = 0
        return events, rest

    def _split(self, *, final: bool) -> list[bytes]:
        buf = self._buf
        size = len(buf)
        events: list[bytes] = []
        start = 0
        pos = self._scan
        while True:
            match = _EVENT_END.search(buf, pos)
            if match is None:
                # A terminator is at most four bytes, so only the last three can
                # still be the beginning of one.
                pos = max(start, size - 3)
                break
            end = match.end()
            if end == size and buf[end - 1] == _CR and not final:
                # The terminator may still grow by an LF that has not arrived.
                pos = match.start()
                break
            events.append(bytes(buf[start:end]))
            start = pos = end
        if start:
            del buf[:start]
        self._scan = pos - start
        return events


def event_data(event: bytes) -> str | None:
    """The data of one event: its ``data:`` lines joined by newlines, or None."""
    try:
        text = event.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines: list[str] = []
    for line in _LINE_END.split(text):
        if line.startswith("data:"):
            value = line[5:]
            lines.append(value[1:] if value.startswith(" ") else value)
    return "\n".join(lines) if lines else None


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int


def usage_from_object(obj: Any) -> TokenUsage | None:
    """Token counts from a response object's ``usage``, or None.

    Chat reports ``prompt_tokens`` and ``completion_tokens``. Embeddings and
    rerank report prompt tokens or only ``total_tokens``.
    """
    if not isinstance(obj, dict):
        return None
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = _count(usage.get("prompt_tokens"))
    completion = _count(usage.get("completion_tokens"))
    total = _count(usage.get("total_tokens"))
    if prompt is None and total is not None:
        prompt = max(total - (completion or 0), 0)
    if prompt is None and completion is None:
        return None
    return TokenUsage(prompt_tokens=prompt or 0, completion_tokens=completion or 0)


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


_USAGE_KEY = re.compile(rb'"usage"\s*:\s*\{')
_FULL_PARSE_LIMIT = 1024 * 1024
_SCAN_WINDOW = 8192


def usage_from_body(body: bytes) -> TokenUsage | None:
    """Best-effort token counts from a non-streaming JSON response body.

    Small bodies are parsed whole. A large one (a batch of embeddings can be many
    megabytes) is only searched near its ends for the ``usage`` object, so the
    event loop is not blocked by parsing megabytes of floats.
    """
    if not body:
        return None
    if len(body) <= _FULL_PARSE_LIMIT:
        if b'"usage"' not in body:
            return None
        try:
            return usage_from_object(json.loads(body))
        except (ValueError, RecursionError):
            return None
    # The usage object sits at the end of the body (or, rarely, at the start).
    for window, pick in ((body[-_SCAN_WINDOW:], -1), (body[:_SCAN_WINDOW], 0)):
        matches = list(_USAGE_KEY.finditer(window))
        if not matches:
            continue
        try:
            text = window[matches[pick].end() - 1 :].decode("utf-8", errors="replace")
            usage, _ = json.JSONDecoder().raw_decode(text)
        except (ValueError, RecursionError):
            continue
        result = usage_from_object({"usage": usage})
        if result is not None:
            return result
    return None


class StreamInspector:
    """Looks at each relayed event: reads usage, spots error events, and says
    whether the event goes to the client.

    With ``strip_usage_only`` the usage-only event (``usage`` present, ``choices``
    empty) is left out, because the router asked the backend for it and the client
    did not. Nothing else is ever dropped, and no event is ever rewritten.
    """

    def __init__(self, *, strip_usage_only: bool) -> None:
        self.strip_usage_only = strip_usage_only
        self.usage: TokenUsage | None = None
        self.error_seen = False
        # The stream's own end marker has been relayed: nothing of value can follow.
        self.done_seen = False

    def keep(self, event: bytes) -> bool:
        if b"[DONE]" in event and event_data(event) == "[DONE]":
            self.done_seen = True
            return True
        if b'"usage"' not in event and b'"error"' not in event:
            return True
        data = event_data(event)
        if data is None:
            return True
        try:
            obj = json.loads(data)
        except (ValueError, RecursionError):
            return True
        if not isinstance(obj, dict):
            return True
        if obj.get("error"):
            self.error_seen = True
            return True
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            return True
        found = usage_from_object(obj)
        if found is not None:
            self.usage = found
        return not (self.strip_usage_only and not obj.get("choices"))
