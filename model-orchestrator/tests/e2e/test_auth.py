"""Section 5 "Auth": bearer keys from Postgres, the allowlist, key refresh, the order of checks."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.harness import world as w
from tests.harness.stack import route, user
from tests.harness.util import error_of, wait_until

CHAT = "/v1/chat/completions"
NOW = dt.datetime.now(dt.UTC)

# One valid request per authenticated endpoint: (name, path, request kwargs).
AUDIO = {"file": ("a.wav", b"RIFF0000", "audio/wav")}
ENDPOINTS = [
    ("chat", CHAT, {"json": {"model": w.CHAT_ALIAS, "messages": user("hi")}}),
    ("chat-stream", CHAT, {"json": {"model": w.CHAT_ALIAS, "messages": user("hi"), "stream": True}}),
    ("embeddings", "/v1/embeddings", {"json": {"model": w.EMBED_ALIAS, "input": "hi"}}),
    ("rerank", "/v1/rerank", {"json": {"model": w.RERANK_ALIAS, "query": "q", "documents": ["a"]}}),
    ("transcriptions", "/v1/audio/transcriptions", {"data": {"model": w.STT_ALIAS}, "files": AUDIO}),
    ("speech", "/v1/audio/speech", {"json": {"model": w.TTS_ALIAS, "input": "hi"}}),
]
ENDPOINT_IDS = [e[0] for e in ENDPOINTS]


@pytest.fixture(scope="module")
def keys(_stack):
    """Keys of every kind, created once for this module."""
    db = _stack.db
    made = {
        "disabled": db.create_key(enabled=False),
        "expired": db.create_key(expires_at=NOW - dt.timedelta(seconds=30)),
        "expires-later": db.create_key(expires_at=NOW + dt.timedelta(days=1)),
        "chat-only": db.create_key(allowed=[w.CHAT_ROUTE]),
        "embed-and-stt": db.create_key(allowed=[w.EMBED_ROUTE, w.STT_ROUTE]),
        "alias-in-allowlist": db.create_key(allowed=[w.CHAT_ALIAS]),  # an alias is not a route name
        "empty-allowlist": db.create_key(allowed=[]),
        "unknown-route": db.create_key(allowed=["No-Such-Route"]),
    }
    _stack.sync_keys()
    return made


# ---- 401 -------------------------------------------------------------------


@pytest.mark.parametrize(("name", "path", "kwargs"), ENDPOINTS, ids=ENDPOINT_IDS)
def test_every_endpoint_requires_a_key(stack, world, name, path, kwargs):
    r = stack.post(path, key=None, **kwargs)

    assert r.status_code == 401
    assert error_of(r)["type"] == "authentication_error"
    assert not [call for fake in world.fakes for call in fake.calls() if call.method == "POST"]


@pytest.mark.parametrize(("name", "path", "kwargs"), ENDPOINTS, ids=ENDPOINT_IDS)
def test_every_endpoint_accepts_a_valid_key(stack, world, name, path, kwargs):
    assert stack.post(path, **kwargs).status_code == 200


def test_missing_unknown_disabled_and_expired_keys_get_the_same_401(stack, world, keys):
    body = {"model": w.CHAT_ALIAS, "messages": user("hi")}
    answers = {
        "missing": stack.post(CHAT, key=None, json=body),
        "bearer without token": stack.post(CHAT, key=None, headers={"Authorization": "Bearer"}, json=body),
        "not bearer": stack.post(
            CHAT, key=None, headers={"Authorization": f"Basic {stack.key.token}"}, json=body
        ),
        "bare token": stack.post(CHAT, key=None, headers={"Authorization": stack.key.token}, json=body),
        "unknown": stack.post(CHAT, key="sk-mo-" + "0" * 40, json=body),
        "prefix of a real key": stack.post(CHAT, key=stack.key.token[:-1], json=body),
        "disabled": stack.post(CHAT, key=keys["disabled"], json=body),
        "expired": stack.post(CHAT, key=keys["expired"], json=body),
    }

    for case, r in answers.items():
        assert r.status_code == 401, case
    assert len({r.text for r in answers.values()}) == 1, {case: r.text for case, r in answers.items()}
    assert not world.chat.calls(CHAT)


def test_key_with_a_future_expiry_works(stack, world, keys):
    assert stack.chat(w.CHAT_ALIAS, key=keys["expires-later"]).status_code == 200


def test_key_is_checked_before_the_body_is_looked_at(stack, world):
    r = stack.post(CHAT, key=None, content=b"{not json", headers={"Content-Type": "application/json"})

    assert r.status_code == 401


def test_open_endpoints_need_no_key(stack, world):
    for path in ("/livez", "/health", "/v1/models", f"/v1/models/{w.CHAT_ALIAS}", "/v1/audio/voices"):
        assert stack.get(path, key=None).status_code == 200, path


# ---- allowlist -------------------------------------------------------------


def test_allowlist_names_routes_and_covers_all_their_model_ids(stack, world, keys):
    for model in (w.CHAT_ALIAS, w.CHAT_ALIAS_2, w.CHAT_BACKEND_MODEL, w.CHAT_DISCOVERED):
        assert stack.chat(model, key=keys["chat-only"]).status_code == 200, model


def test_allowlist_violation_is_403(stack, world, keys):
    r = stack.chat(w.REASONER_ALIAS, key=keys["chat-only"])

    assert r.status_code == 403
    assert error_of(r)["type"] == "permission_error"
    assert not world.reasoner.calls(CHAT)


@pytest.mark.parametrize(("name", "path", "kwargs"), ENDPOINTS, ids=ENDPOINT_IDS)
def test_allowlist_applies_on_every_endpoint(stack, world, keys, name, path, kwargs):
    allowed = name in ("embeddings", "transcriptions")

    r = stack.post(path, key=keys["embed-and-stt"], **kwargs)

    assert r.status_code == (200 if allowed else 403)


def test_allowlist_holds_route_names_not_aliases(stack, world, keys):
    assert stack.chat(w.CHAT_ALIAS, key=keys["alias-in-allowlist"]).status_code == 403
    assert stack.chat(w.CHAT_ALIAS, key=keys["unknown-route"]).status_code == 403


def test_empty_allowlist_means_no_restriction(stack, world, keys):
    """NULL and ``{}`` both mean "all routes" (the admin UI writes NULL)."""
    assert stack.chat(w.CHAT_ALIAS, key=keys["empty-allowlist"]).status_code == 200
    assert stack.chat(w.REASONER_ALIAS, key=keys["empty-allowlist"]).status_code == 200


# ---- order of checks: auth, model (404), type (400), allowlist (403) ---------


def test_unknown_model_is_404_even_for_a_restricted_key(stack, world, keys):
    assert stack.chat("no-such-model", key=keys["chat-only"]).status_code == 404


def test_wrong_type_is_reported_before_the_allowlist(stack, world, keys):
    # embed-delta is neither a chat model nor on this key's allowlist: the type check comes first
    assert stack.chat(w.EMBED_ALIAS, key=keys["chat-only"]).status_code == 400


def test_bad_key_is_reported_before_an_unknown_model(stack, world):
    assert stack.chat("no-such-model", key="sk-mo-nope").status_code == 401


# ---- key refresh -----------------------------------------------------------


def test_new_key_is_picked_up_without_a_restart(stack, world):
    key = stack.db.create_key()

    wait_until(lambda: stack.authenticates(key), timeout=5, what="the new key to be accepted")
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 200


def test_disabling_and_deleting_a_key_take_effect(stack, world):
    first, second = stack.db.create_key(), stack.db.create_key()
    stack.sync_keys()
    assert stack.authenticates(first) and stack.authenticates(second)

    stack.db.update_key(first, enabled=False)
    stack.db.delete_key(second)

    wait_until(lambda: not stack.authenticates(first), timeout=5, what="the disabled key to be refused")
    wait_until(lambda: not stack.authenticates(second), timeout=5, what="the deleted key to be refused")
    stack.db.update_key(first, enabled=True)
    wait_until(lambda: stack.authenticates(first), timeout=5, what="the re-enabled key to be accepted")


def test_key_expiry_is_enforced_at_request_time(stack, world):
    """A key that expires between two refreshes stops working without a database round trip."""
    key = stack.new_key(expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1.5))
    assert stack.authenticates(key)

    wait_until(lambda: not stack.authenticates(key), timeout=4, interval=0.1, what="the key to expire")


def test_allowlist_change_is_picked_up(stack, world):
    key = stack.new_key(allowed=[w.EMBED_ROUTE])
    assert stack.chat(w.CHAT_ALIAS, key=key).status_code == 403

    stack.db.update_key(key, allowed_models=[w.EMBED_ROUTE, w.CHAT_ROUTE])

    wait_until(
        lambda: stack.chat(w.CHAT_ALIAS, key=key).status_code == 200, timeout=5, what="the wider allowlist"
    )


# ---- empty key table -------------------------------------------------------


def test_empty_key_table_denies_everything(factory):
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack([route("Solo", "chat", backend, models=["solo"])])
    stack.db.execute("DELETE FROM api_keys")
    wait_until(lambda: not stack.authenticates(stack.key), timeout=5, what="the last key to be forgotten")

    assert stack.chat("solo").status_code == 401
    assert stack.chat("solo", key=None).status_code == 401
    assert stack.chat("solo", key="anything").status_code == 401
    assert stack.get("/v1/models", key=None).status_code == 200  # the open endpoints stay open
    assert not backend.calls(CHAT)
