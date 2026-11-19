"""Signed cookie sessions.

The session lives entirely in one cookie: a JSON payload signed with
HMAC-SHA256 (itsdangerous). It carries the signed-in user, the CSRF token
and three timestamps. Nothing in it is secret from its owner, so it is
signed, not encrypted.

Expiry is enforced on the server from the payload, never from the cookie's
own Max-Age: ``exp`` is the absolute end of the session, ``seen`` drives the
idle timeout.

A signed-in session is bound to the credential it was made with (``bind``):
the local account's user name and password, or the single sign-on
configuration. Change either and the sessions made with the old one are
refused, even though their signature is still good.

Signed-out sessions are refused through ``revocations.Revocations``, which
keeps their ids in Postgres; this module only decodes and validates.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from itsdangerous import BadData, URLSafeSerializer


def credential_binding(secret: str, *parts: str) -> str:
    """A fingerprint of a credential, safe to put into the signed cookie.

    Keyed with the session secret, so the cookie's owner cannot use it to
    test password guesses offline.
    """
    message = b"mo-admin.binding.v1\x00" + "\x00".join(parts).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]

ANON_LIFETIME = 3600  # a not-yet-signed-in session only holds a CSRF token
REFRESH_AFTER = 300   # re-issue the cookie when ``seen`` is older than this


@dataclass
class Session:
    data: dict[str, Any] = field(default_factory=dict)
    dirty: bool = False
    cleared: bool = False

    @property
    def user(self) -> str | None:
        return self.data.get("user")

    @property
    def display_name(self) -> str:
        return self.data.get("name") or self.data.get("user") or ""

    @property
    def method(self) -> str | None:
        return self.data.get("method")

    @property
    def sid(self) -> str | None:
        return self.data.get("sid")

    @property
    def subject(self) -> str | None:
        """The identity provider's stable id of the user (OIDC ``sub``)."""
        return self.data.get("sub")

    @property
    def actor(self) -> str | None:
        """Who to name in the audit log: ``local:<user>`` or ``oidc:<username>``.

        The prefix keeps the local account apart from an OIDC user who
        happens to have the same name.
        """
        user = self.user
        return f"{self.method}:{user}" if user else None

    @property
    def expires(self) -> int:
        return int(self.data.get("exp", 0))

    @property
    def csrf(self) -> str:
        """The CSRF token, created on first use (which also creates the cookie)."""
        token = self.data.get("csrf")
        if not token:
            now = int(time.time())
            token = secrets.token_urlsafe(32)
            self.data.setdefault("sid", secrets.token_urlsafe(18))
            self.data.setdefault("iat", now)
            self.data.setdefault("exp", now + ANON_LIFETIME)
            self.data["csrf"] = token
            self.dirty = True
        return token

    def flash(self, kind: str, text: str) -> None:
        self.data.setdefault("flash", []).append([kind, text])
        self.dirty = True

    def pop_flashes(self) -> list[tuple[str, str]]:
        items = self.data.pop("flash", None)
        if not items:
            return []
        self.dirty = True
        return [(kind, text) for kind, text in items]


class SessionManager:
    def __init__(
        self,
        secret: str,
        *,
        lifetime_seconds: int,
        idle_seconds: int,
        bindings: dict[str, str | None] | None = None,
    ):
        """``bindings`` maps a sign-in method ("local", "oidc") to the
        fingerprint of its current credential, or to ``None`` when that
        method is switched off. Sessions of a method that is off, or whose
        fingerprint differs, are refused."""
        self._serializer = URLSafeSerializer(
            secret,
            salt="mo-admin.session.v1",
            signer_kwargs={"digest_method": hashlib.sha256, "key_derivation": "hmac"},
        )
        self.lifetime = lifetime_seconds
        self.idle = idle_seconds
        self._bindings = bindings

    # -- cookie <-> session -------------------------------------------------
    def load(self, cookie: str | None, *, passive: bool = False) -> Session:
        """Decode a cookie. Anything invalid or expired yields an empty session.

        ``passive`` marks background polling: it must not count as activity,
        or an open tab would keep a session alive until its absolute expiry.
        """
        if not cookie:
            return Session()
        try:
            data = self._serializer.loads(cookie)
        except BadData:
            return Session(cleared=True)
        if not isinstance(data, dict):
            return Session(cleared=True)
        now = int(time.time())
        if int(data.get("exp", 0)) <= now:
            return Session(cleared=True)
        if not isinstance(data.get("sid"), str):
            return Session(cleared=True)
        session = Session(data=data)
        if data.get("user"):
            if not self._bound(data):
                return Session(cleared=True)
            seen = int(data.get("seen", 0))
            if seen + self.idle <= now:
                return Session(cleared=True)
            if not passive and now - seen >= REFRESH_AFTER:
                data["seen"] = now
                session.dirty = True
        return session

    def _binding(self, method: Any) -> str | None:
        if self._bindings is None:  # binding not in use (unit tests of the cookie format)
            return ""
        return self._bindings.get(method) if isinstance(method, str) else None

    def _bound(self, data: dict[str, Any]) -> bool:
        expected = self._binding(data.get("method"))
        if expected is None:
            return False
        found = data.get("bind", "")
        return isinstance(found, str) and hmac.compare_digest(found.encode("utf-8"), expected.encode("utf-8"))

    def dump(self, session: Session) -> str:
        return self._serializer.dumps(session.data)

    def max_age(self, session: Session) -> int:
        return max(0, int(session.data.get("exp", 0)) - int(time.time()))

    # -- transitions --------------------------------------------------------
    def sign_in(
        self,
        session: Session,
        *,
        user: str,
        name: str | None,
        email: str | None,
        method: str,
        subject: str | None = None,
    ) -> None:
        """Start a fresh session: new id and CSRF token, nothing carried over."""
        now = int(time.time())
        session.data = {
            "sid": secrets.token_urlsafe(18),
            "csrf": secrets.token_urlsafe(32),
            "user": user,
            "name": name or user,
            "email": email,
            "method": method,
            "sub": subject,
            "bind": self._binding(method) or "",
            "iat": now,
            "seen": now,
            "exp": now + self.lifetime,
        }
        session.dirty = True
        session.cleared = False

    @staticmethod
    def sign_out(session: Session) -> None:
        """Forget the session in this browser. Revoking its id, so that a
        copy of the cookie dies too, is the caller's job (``Revocations``)."""
        session.data = {}
        session.dirty = False
        session.cleared = True
