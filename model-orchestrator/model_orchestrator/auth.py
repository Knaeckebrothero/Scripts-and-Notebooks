"""Bearer key to key record.

Keys are stored as the unsalted SHA-256 hex of the full token (unchanged, so
existing keys keep working). The token itself is hashed on arrival and not kept:
everything downstream works with the ``KeyRecord``.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

AUTH_ERROR_MESSAGE = "Invalid, disabled, or expired API key"


@dataclass(frozen=True)
class KeyRecord:
    id: int
    name: str
    owner: str
    key_prefix: str
    rate_limit_rpm: int
    max_concurrency: int | None
    # Route names. None means every route (an empty array in the table counts as NULL).
    allowed_models: frozenset[str] | None
    enabled: bool
    expires_at: datetime | None

    def usable(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        return self.expires_at is None or self.expires_at > now

    def allows(self, route_name: str) -> bool:
        return not self.allowed_models or route_name in self.allowed_models


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bearer_token(authorization: str | None) -> str | None:
    """The token of an ``Authorization: Bearer <token>`` header, or None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization[7:]


def key_from_row(row: Mapping[str, Any]) -> tuple[str, KeyRecord]:
    allowed = row.get("allowed_models")
    expires_at = row.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    cap = row.get("max_concurrency")
    record = KeyRecord(
        id=int(row["id"]),
        name=str(row["name"]),
        owner=str(row["owner"]),
        key_prefix=str(row.get("key_prefix") or ""),
        rate_limit_rpm=int(row["rate_limit_rpm"]),
        max_concurrency=int(cap) if cap is not None else None,
        allowed_models=frozenset(allowed) if allowed else None,
        enabled=bool(row["enabled"]),
        expires_at=expires_at,
    )
    return str(row["key_hash"]), record


class KeyCache:
    """The key set as last read from the database.

    It is replaced as a whole on every refresh. When the database goes away the
    last set stays, so requests keep authenticating (degraded mode). An empty set
    denies everything.
    """

    def __init__(self) -> None:
        self._by_hash: dict[str, KeyRecord] = {}
        self._loaded_at: float | None = None

    def __len__(self) -> int:
        return len(self._by_hash)

    def replace(self, records: Mapping[str, KeyRecord]) -> None:
        self._by_hash = dict(records)
        self._loaded_at = time.monotonic()

    def age_seconds(self) -> float | None:
        return None if self._loaded_at is None else time.monotonic() - self._loaded_at

    def authenticate(self, authorization: str | None, now: datetime | None = None) -> KeyRecord | None:
        """The key record for a request, or None when it must get 401.

        Missing header, unknown key, disabled key and expired key are not told apart.
        """
        token = bearer_token(authorization)
        if token is None:
            return None
        record = self._by_hash.get(hash_token(token))
        if record is None or not record.usable(now or datetime.now(UTC)):
            return None
        return record
