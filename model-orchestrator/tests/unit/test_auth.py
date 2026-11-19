"""Bearer key to key record (contract section 5, design 7.4)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from model_orchestrator.auth import KeyCache, KeyRecord, bearer_token, hash_token, key_from_row

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def row(token: str, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 1,
        "key_hash": hash_token(token),
        "key_prefix": token[:7],
        "name": "test key",
        "owner": "alice",
        "rate_limit_rpm": 100,
        "allowed_models": None,
        "enabled": True,
        "expires_at": None,
        "max_concurrency": None,
    }
    base.update(overrides)
    return base


def cache_of(*rows: dict[str, object]) -> KeyCache:
    cache = KeyCache()
    cache.replace(dict(key_from_row(r) for r in rows))
    return cache


def test_hash_is_unsalted_sha256_hex_of_the_full_token() -> None:
    assert hash_token("sk-abc") == hashlib.sha256(b"sk-abc").hexdigest()


@pytest.mark.parametrize(
    ("header", "token"),
    [
        ("Bearer sk-abc", "sk-abc"),
        ("Bearer  sk-abc", " sk-abc"),
        ("Bearer ", ""),
        (None, None),
        ("", None),
        ("sk-abc", None),
        ("bearer sk-abc", None),
        ("Basic c2s6YWJj", None),
        ("Bearer", None),
    ],
)
def test_bearer_token(header: str | None, token: str | None) -> None:
    assert bearer_token(header) == token


def test_valid_key() -> None:
    record = cache_of(row("sk-abc")).authenticate("Bearer sk-abc", NOW)
    assert isinstance(record, KeyRecord)
    assert (record.id, record.name, record.owner, record.rate_limit_rpm) == (1, "test key", "alice", 100)
    assert record.max_concurrency is None and record.allowed_models is None


@pytest.mark.parametrize("header", [None, "", "Bearer wrong", "Bearer ", "sk-abc", "bearer sk-abc"])
def test_missing_and_unknown_keys_are_rejected(header: str | None) -> None:
    assert cache_of(row("sk-abc")).authenticate(header, NOW) is None


def test_disabled_key_is_rejected() -> None:
    assert cache_of(row("sk-abc", enabled=False)).authenticate("Bearer sk-abc", NOW) is None


def test_expiry() -> None:
    expired = cache_of(row("sk-abc", expires_at=NOW - timedelta(seconds=1)))
    at_the_instant = cache_of(row("sk-abc", expires_at=NOW))
    still_good = cache_of(row("sk-abc", expires_at=NOW + timedelta(seconds=1)))
    assert expired.authenticate("Bearer sk-abc", NOW) is None
    assert at_the_instant.authenticate("Bearer sk-abc", NOW) is None
    assert still_good.authenticate("Bearer sk-abc", NOW) is not None


def test_naive_expiry_is_read_as_utc() -> None:
    cache = cache_of(row("sk-abc", expires_at=datetime(2026, 11, 18, 11, 0)))
    assert cache.authenticate("Bearer sk-abc", NOW) is None


def test_an_empty_key_table_denies_everything() -> None:
    cache = KeyCache()
    assert len(cache) == 0
    assert cache.authenticate("Bearer anything", NOW) is None
    assert cache.authenticate("Bearer ", NOW) is None


def test_allowlist_holds_route_names() -> None:
    record = cache_of(row("sk-abc", allowed_models=["Chat-Pool", "Embed"])).authenticate("Bearer sk-abc", NOW)
    assert record is not None
    assert record.allows("Chat-Pool") and record.allows("Embed")
    assert not record.allows("Rerank")
    assert not record.allows("chat-pool")


@pytest.mark.parametrize("allowed", [None, []])
def test_null_or_empty_allowlist_means_every_route(allowed: list[str] | None) -> None:
    record = cache_of(row("sk-abc", allowed_models=allowed)).authenticate("Bearer sk-abc", NOW)
    assert record is not None and record.allowed_models is None and record.allows("anything")


def test_max_concurrency_is_read() -> None:
    record = cache_of(row("sk-abc", max_concurrency=4)).authenticate("Bearer sk-abc", NOW)
    assert record is not None and record.max_concurrency == 4


def test_replace_swaps_the_whole_set_and_tracks_age() -> None:
    cache = KeyCache()
    assert cache.age_seconds() is None
    cache.replace(dict([key_from_row(row("sk-old"))]))
    assert cache.authenticate("Bearer sk-old", NOW) is not None
    cache.replace(dict([key_from_row(row("sk-new", id=2))]))
    assert cache.authenticate("Bearer sk-old", NOW) is None
    assert cache.authenticate("Bearer sk-new", NOW) is not None
    age = cache.age_seconds()
    assert age is not None and 0 <= age < 5


def test_the_token_is_not_kept() -> None:
    cache = cache_of(row("sk-secret-token"))
    record = cache.authenticate("Bearer sk-secret-token", NOW)
    assert "sk-secret-token" not in repr(record)
    assert "sk-secret-token" not in repr(vars(cache))
