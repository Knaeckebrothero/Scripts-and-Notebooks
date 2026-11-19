"""Request-level security helpers: scheme and client address behind a proxy,
login throttling, response headers and safe redirect targets."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from starlette.requests import Request

from .config import Settings

def content_security_policy(form_targets: tuple[str, ...] = ()) -> str:
    """No inline script or style anywhere, so no 'unsafe-inline' and no nonces.

    ``form_targets`` are extra origins a form submission may end up at. The
    sign-out form redirects to the identity provider's end-session endpoint,
    and browsers apply ``form-action`` to that redirect too.
    """
    return "; ".join(
        [
            "default-src 'none'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            " ".join(["form-action 'self'", *form_targets]),
            "base-uri 'none'",
            "frame-ancestors 'none'",
        ]
    )


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
}


def _first(value: str | None) -> str:
    return (value or "").split(",")[0].strip()


def is_https(request: Request, settings: Settings) -> bool:
    if settings.public_url:
        return settings.public_url.startswith("https://")
    if settings.trust_proxy_headers:
        proto = _first(request.headers.get("x-forwarded-proto")).lower()
        if proto:
            return proto == "https"
    return request.url.scheme == "https"


def external_base(request: Request, settings: Settings) -> str:
    """Scheme and host as the browser sees them, without a trailing slash."""
    if settings.public_url:
        return settings.public_url
    scheme = "https" if is_https(request, settings) else "http"
    host = ""
    if settings.trust_proxy_headers:
        host = _first(request.headers.get("x-forwarded-host"))
    host = host or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


def client_address(request: Request, settings: Settings) -> str:
    """The address login throttling keys on.

    Behind a reverse proxy the last X-Forwarded-For entry is the one our own
    proxy added, which a client cannot forge. Everything before it can be
    anything the client sent, including whole header lines of its own: some
    proxies append to the client's line, others add a second line, so all
    lines are read and the very last entry wins.
    """
    if settings.trust_proxy_headers:
        entries = [
            entry.strip()
            for line in request.headers.getlist("x-forwarded-for")
            for entry in line.split(",")
            if entry.strip()
        ]
        if entries:
            return entries[-1]
    return request.client.host if request.client else "unknown"


def constant_time_equals(a: str, b: str) -> bool:
    """Compare two strings without leaking where, or at what length, they differ.

    Works on digests of the UTF-8 bytes, so any text is acceptable input:
    ``hmac.compare_digest`` itself raises on non-ASCII strings.
    """
    return hmac.compare_digest(
        hashlib.sha256(a.encode("utf-8")).digest(),
        hashlib.sha256(b.encode("utf-8")).digest(),
    )


def safe_next(target: str | None) -> str:
    """Only same-site paths are valid post-login destinations."""
    if not target or not target.startswith("/"):
        return "/"
    if target.startswith("//") or target.startswith("/\\") or "\\" in target:
        return "/"
    if any(ord(ch) < 0x20 for ch in target):
        return "/"
    return target


@dataclass
class _Attempts:
    failures: int = 0
    last_failure: float = 0.0
    locked_until: float = 0.0
    strikes: int = 0


class LoginThrottle:
    """Lock a source out after repeated failed sign-ins.

    ``max_attempts`` failures in a row lock the source for ``lockout``
    seconds; every further lock doubles, up to ``max_lockout``. A success or
    a quiet ``window`` resets the count. State is in memory: it is per
    process and empty after a restart, which is fine for slowing a guesser
    down but is not an account lockout.
    """

    MAX_SOURCES = 10_000

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        lockout: float = 60.0,
        max_lockout: float = 900.0,
        window: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_attempts = max_attempts
        self.lockout = lockout
        self.max_lockout = max_lockout
        self.window = window
        self._clock = clock
        self._state: dict[str, _Attempts] = {}

    def retry_after(self, key: str) -> float:
        entry = self._state.get(key)
        if entry is None:
            return 0.0
        return max(0.0, entry.locked_until - self._clock())

    def failure(self, key: str) -> float:
        """Record a failed attempt; returns the lock duration if it triggered one."""
        now = self._clock()
        if len(self._state) >= self.MAX_SOURCES:
            self._prune(now)
        entry = self._state.setdefault(key, _Attempts())
        if now - entry.last_failure > self.window and entry.locked_until <= now:
            entry.failures = 0
            entry.strikes = 0
        entry.failures += 1
        entry.last_failure = now
        if entry.failures >= self.max_attempts:
            duration = min(self.max_lockout, self.lockout * (2 ** entry.strikes))
            entry.locked_until = now + duration
            entry.strikes += 1
            entry.failures = 0
            return duration
        return 0.0

    def success(self, key: str) -> None:
        self._state.pop(key, None)

    def _prune(self, now: float) -> None:
        stale = [
            k for k, e in self._state.items()
            if e.locked_until <= now and now - e.last_failure > self.window
        ]
        for k in stale:
            del self._state[k]
        if len(self._state) > self.MAX_SOURCES:
            # Still too many: someone is cycling through addresses. Memory
            # matters more than their counters, so the oldest half goes.
            oldest = sorted(self._state, key=lambda k: self._state[k].last_failure)
            for k in oldest[: len(oldest) // 2]:
                del self._state[k]


class LoginSlowdown:
    """Slow every local sign-in down while failed attempts pile up, from
    whatever source they come.

    This is the answer to a guesser who rotates source addresses, and it is
    deliberately only a delay: a lock here could be held shut by anyone, and
    the local account is the one that must work when everything else is
    down. Below ``threshold`` recent failures there is no delay; above it
    the delay grows by ``step`` per failure up to ``max_delay``. The correct
    password is delayed like any other attempt and then accepted.
    """

    def __init__(
        self,
        *,
        threshold: int = 20,
        step: float = 0.25,
        max_delay: float = 4.0,
        window: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.threshold = threshold
        self.step = step
        self.max_delay = max_delay
        self.window = window
        self.sleep = sleep
        self._clock = clock
        # Only the most recent failures matter; the cap bounds memory.
        self._failures: deque[float] = deque(maxlen=threshold + int(max_delay / step) + 1)

    def record_failure(self) -> None:
        self._failures.append(self._clock())

    def delay(self) -> float:
        horizon = self._clock() - self.window
        while self._failures and self._failures[0] < horizon:
            self._failures.popleft()
        excess = len(self._failures) - self.threshold + 1
        return min(self.max_delay, self.step * excess) if excess > 0 else 0.0

    async def wait(self) -> None:
        seconds = self.delay()
        if seconds > 0:
            await self.sleep(seconds)


class OncePer:
    """``first(key)`` is true at most once per ``interval`` seconds per key.

    Used to keep an event that an outsider can trigger at will (a refused
    sign-in) from filling the audit log.
    """

    def __init__(self, interval: float = 600.0, *, clock: Callable[[], float] = time.monotonic, limit: int = 5000):
        self.interval = interval
        self.limit = limit
        self._clock = clock
        self._seen: dict[str, float] = {}

    def first(self, key: str) -> bool:
        now = self._clock()
        last = self._seen.get(key)
        if last is not None and now - last < self.interval:
            return False
        if len(self._seen) >= self.limit:
            self._seen = {k: t for k, t in self._seen.items() if now - t < self.interval}
            if len(self._seen) >= self.limit:
                self._seen.clear()
        self._seen[key] = now
        return True
