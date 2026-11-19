"""Small helpers: free ports, polling, SSE splitting, error-envelope checks."""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Callable
from typing import Any

import httpx

HOST = "127.0.0.1"


def free_port(host: str = HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def port_is_free(port: int, host: str = HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


def can_connect(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class WaitTimeout(AssertionError):
    """A polled condition did not come true in time (an assertion failure, not an error)."""


def wait_until[T](
    probe: Callable[[], T],
    *,
    timeout: float = 5.0,
    interval: float = 0.05,
    what: str = "condition",
) -> T:
    """Poll ``probe`` until it returns something truthy and return that.

    Exceptions from ``probe`` that are assertion failures or transport errors
    count as "not yet". Raises :class:`WaitTimeout` with the last value or
    error when the deadline passes.
    """
    deadline = time.monotonic() + timeout
    last: Any = None
    while True:
        try:
            last = probe()
            if last:
                return last
        except (AssertionError, httpx.TransportError) as exc:
            last = exc
        if time.monotonic() >= deadline:
            raise WaitTimeout(f"timed out after {timeout:.1f}s waiting for {what}; last: {last!r}")
        time.sleep(interval)


def holds_for(
    probe: Callable[[], Any], *, seconds: float, interval: float = 0.1, what: str = "condition"
) -> None:
    """Assert that ``probe`` stays truthy for ``seconds`` (for "nothing changes" checks)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        assert value, f"{what} stopped holding: {value!r}"
        time.sleep(interval)


def sse_events(raw: bytes) -> list[bytes]:
    """Split an SSE byte stream into events, each including its blank-line terminator."""
    events, rest = [], raw
    while rest:
        cut = rest.find(b"\n\n")
        if cut < 0:
            events.append(rest)
            break
        events.append(rest[: cut + 2])
        rest = rest[cut + 2 :]
    return events


def sse_data(raw: bytes) -> list[Any]:
    """The JSON payload of every ``data:`` event; the ``[DONE]`` marker is kept as a string."""
    out: list[Any] = []
    for event in sse_events(raw):
        for line in event.decode("utf-8", "replace").splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            out.append("[DONE]" if payload == "[DONE]" else json.loads(payload))
    return out


def error_of(response: httpx.Response) -> dict[str, Any]:
    """Assert the body is the OpenAI error envelope and return its ``error`` object."""
    try:
        body = response.json()
    except ValueError:
        raise AssertionError(
            f"expected a JSON error envelope, got {response.status_code} "
            f"{response.headers.get('content-type')!r}: {response.text[:200]!r}"
        ) from None
    assert isinstance(body, dict) and isinstance(body.get("error"), dict), (
        f"expected {{'error': {{...}}}}, got {response.status_code}: {body!r}"
    )
    error = body["error"]
    assert isinstance(error.get("message"), str) and error["message"], f"error without message: {body!r}"
    assert isinstance(error.get("type"), str) and error["type"], f"error without type: {body!r}"
    return error


def find_values(obj: Any, key: str) -> list[Any]:
    """Every value stored under ``key`` anywhere inside a JSON structure."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(find_values(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_values(item, key))
    return found
