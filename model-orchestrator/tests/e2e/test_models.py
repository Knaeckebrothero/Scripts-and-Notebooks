"""Section 5 "Model ids": aliases, discovered ids, precedence, ``/v1/models``. C14 and C20."""

from __future__ import annotations

import pytest

from tests.fakes import model_entry
from tests.harness import world as w
from tests.harness.stack import route
from tests.harness.util import error_of

CHAT = "/v1/chat/completions"


# ---- listing ---------------------------------------------------------------


def test_models_lists_every_alias_and_every_discovered_id(stack):
    r = stack.get("/v1/models", key=None)

    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    ids = [entry["id"] for entry in body["data"]]
    assert len(ids) == len(set(ids)), "an id is listed twice"
    assert set(ids) == set(w.ALL_ALIASES) | set(w.ALL_DISCOVERED)


def test_models_needs_no_key_and_ignores_a_bad_one(stack):
    assert stack.get("/v1/models", key=None).status_code == 200
    assert stack.get("/v1/models", key="sk-mo-not-a-real-key").status_code == 200


def test_models_entry_passes_backend_metadata_through(stack):
    """``max_model_len`` and friends come from the backend's own ``/v1/models``."""
    models = stack.models()
    upstream = model_entry(w.VISION_BACKEND_MODEL)

    assert models[w.VISION_BACKEND_MODEL] == {**models[w.VISION_BACKEND_MODEL], **upstream}
    alias = models[w.VISION_ALIAS]
    assert alias["id"] == w.VISION_ALIAS
    assert alias["max_model_len"] == 32768
    assert alias["permission"] == upstream["permission"]
    assert alias["created"] == upstream["created"]
    assert alias["root"] == w.VISION_BACKEND_MODEL
    assert alias["owned_by"] == "vllm"  # no owned_by on this route: the backend's value stays


def test_models_owned_by_from_config_overrides_the_backend(stack):
    models = stack.models()

    assert models[w.CHAT_ALIAS]["owned_by"] == w.OWNED_BY
    assert models[w.CHAT_BACKEND_MODEL]["owned_by"] == w.OWNED_BY
    assert models[w.CHAT_DISCOVERED]["owned_by"] == w.OWNED_BY
    assert models[w.CHAT_ALIAS]["max_model_len"] == 32768


def test_models_entry_for_a_backend_without_model_listing_is_a_stub(stack):
    """Whisper, Kokoro and TEI have no ``/v1/models``; their aliases get a minimal entry."""
    for alias in (w.STT_ALIAS, w.TTS_ALIAS, w.EMBED_ALIAS):
        entry = stack.models()[alias]
        assert entry["id"] == alias
        assert entry["object"] == "model"
        assert isinstance(entry["created"], int)
        assert entry["owned_by"]
        assert "max_model_len" not in entry


def test_get_model_by_id(stack):
    r = stack.get(f"/v1/models/{w.CHAT_ALIAS}", key=None)

    assert r.status_code == 200
    assert r.json() == stack.models()[w.CHAT_ALIAS]


def test_get_unknown_model_is_404(stack):
    r = stack.get("/v1/models/no-such-model", key=None)

    assert r.status_code == 404
    assert error_of(r)["type"] == "invalid_request_error"


@pytest.mark.change("C20")
@pytest.mark.parametrize("path", [w.CHAT_BACKEND_MODEL, w.CHAT_DISCOVERED, "acme%2Fchat-alpha-7b"])
def test_get_model_with_a_slash_in_its_id(stack, path):
    r = stack.get(f"/v1/models/{path}", key=None)

    assert r.status_code == 200, r.text
    expected_id = path.replace("%2F", "/")
    assert r.json().get("id") == expected_id
    assert r.json() == stack.models()[expected_id]


@pytest.mark.legacy_only
def test_get_model_with_a_slash_in_its_id_is_404_today(stack):
    r = stack.get(f"/v1/models/{w.CHAT_BACKEND_MODEL}", key=None)

    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


@pytest.mark.change("C20")
def test_get_unknown_model_with_a_slash_is_an_enveloped_404(stack):
    r = stack.get("/v1/models/acme/no-such-model", key=None)

    assert r.status_code == 404
    error_of(r)


@pytest.mark.change("C14")
def test_models_entries_carry_route_name_and_type(stack):
    models = stack.models()

    for name, (type_, alias) in w.ROUTE_TYPES.items():
        assert models[alias].get("x_router") == {"route": name, "type": type_}, alias
    assert models[w.CHAT_DISCOVERED].get("x_router") == {"route": w.CHAT_ROUTE, "type": "chat"}
    assert models[w.RERANK_BACKEND_MODEL].get("x_router") == {"route": w.RERANK_ROUTE, "type": "reranker"}
    single = stack.get(f"/v1/models/{w.CHAT_ALIAS}", key=None).json()
    assert single.get("x_router") == {"route": w.CHAT_ROUTE, "type": "chat"}


@pytest.mark.legacy_only
def test_models_entries_have_no_x_router_today(stack):
    assert not [entry for entry in stack.models().values() if "x_router" in entry]


# ---- precedence ------------------------------------------------------------


@pytest.mark.parametrize("alias_route_first", [True, False], ids=["alias-route-first", "alias-route-last"])
def test_an_alias_beats_a_discovered_id(factory, alias_route_first):
    """Route A has the alias ``acme/contested``; route B's backend reports that very id."""
    a = factory.fake("vllm", ["acme/a-real"], name="a")
    b = factory.fake("vllm", ["acme/contested", "acme/b-other"], name="b")
    routes = [
        route("Route-A", "chat", a, backend_model_name="acme/a-real", models=["acme/contested"]),
        route("Route-B", "chat", b, models=["b-alias"]),
    ]
    stack = factory.stack(routes if alias_route_first else routes[::-1])

    r = stack.chat("acme/contested")

    assert r.status_code == 200, r.text
    assert a.last(CHAT).json["model"] == "acme/a-real"  # alias hit: rewritten, served by route A
    assert not b.calls(CHAT)
    assert list(stack.models()).count("acme/contested") == 1


def test_first_route_wins_among_discovered_ids(factory):
    first = factory.fake("vllm", ["acme/twin", "acme/only-first"], name="first")
    second = factory.fake("vllm", ["acme/twin", "acme/only-second"], name="second")
    stack = factory.stack(
        [
            route("Twin-One", "chat", first, models=["twin-one"]),
            route("Twin-Two", "chat", second, models=["twin-two"]),
        ]
    )

    for _ in range(3):
        assert stack.chat("acme/twin").status_code == 200

    assert len(first.calls(CHAT)) == 3 and not second.calls(CHAT)
    assert stack.chat("acme/only-second").status_code == 200
    assert second.last(CHAT).json["model"] == "acme/only-second"


def test_alias_without_backend_model_name_is_forwarded_as_is(factory):
    backend = factory.fake("vllm", ["plain-name"])
    stack = factory.stack([route("Plain", "chat", backend, models=["plain-name"])])

    assert stack.chat("plain-name").status_code == 200
    assert backend.last(CHAT).json["model"] == "plain-name"


def test_route_without_aliases_serves_its_discovered_ids(factory):
    """``models:`` is optional: a route can live on discovery alone."""
    backend = factory.fake("vllm", ["acme/discovered-only"])
    stack = factory.stack([route("Bare", "chat", backend)])

    assert set(stack.models()) == {"acme/discovered-only"}
    assert stack.chat("acme/discovered-only").status_code == 200


def test_only_the_first_backend_of_a_pool_is_asked_for_ids(factory):
    """Pools are assumed to serve the same model; an id only the second backend reports is unknown."""
    one = factory.fake("vllm", ["acme/pooled"], name="one")
    two = factory.fake("vllm", ["acme/pooled", "acme/only-on-second"], name="two")
    stack = factory.stack(
        [route("Pool", "chat", [one, two], backend_model_name="acme/pooled", models=["pooled"])]
    )

    assert set(stack.models()) == {"pooled", "acme/pooled"}
    assert stack.chat("acme/only-on-second").status_code == 404


def test_same_backend_behind_two_routes(factory):
    """Two routes may share a backend; the first one owns the discovered ids."""
    backend = factory.fake("vllm", ["acme/shared"])
    stack = factory.stack(
        [
            route("Shared-Plain", "chat", backend, backend_model_name="acme/shared", models=["shared-plain"]),
            route(
                "Shared-Tuned", "chat", backend, backend_model_name="acme/shared", models=["shared-tuned"],
                request_defaults={"temperature": 0.1},
            ),
        ]
    )  # fmt: skip

    assert stack.chat("shared-tuned").status_code == 200
    assert backend.last(CHAT).json.get("temperature") == 0.1
    assert stack.chat("shared-plain").status_code == 200
    assert "temperature" not in backend.last(CHAT).json
    assert stack.chat("acme/shared").status_code == 200
    assert "temperature" not in backend.last(CHAT).json  # discovered id belongs to the first route
