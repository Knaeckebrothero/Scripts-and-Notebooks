"""Upstream-router mode (design 7.9): one router reaches another site's models through that site's router.

Two real router instances: *site A* owns the models, *site B* lists A as an
upstream. New router only.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeKokoro, FakeVLLM
from tests.harness.stack import route, user
from tests.harness.util import error_of, holds_for, wait_until

CHAT = "/v1/chat/completions"
pytestmark = pytest.mark.feature("upstream_mode")
SETTLE = 12  # seconds for probes and upstream discovery (1 s intervals) to settle
A_DEFAULTS = {"temperature": 0.6}


class TwoSites:
    def __init__(self, factory, *, upstream_extra: dict | None = None, a_target: str | None = None):
        # ---- site A: the models live here
        self.a_chat = factory.fake("vllm", ["acme/a-chat-7b"], name="a-chat")
        self.a_shared = factory.fake("vllm", ["acme/a-shared"], name="a-shared")
        self.a_embed = factory.fake("tei", name="a-embed")
        self.a_tts = factory.fake("kokoro", name="a-tts")
        self.a = factory.stack(
            [
                route("A-Chat", "chat", self.a_chat, backend_model_name="acme/a-chat-7b", models=["a-chat"],
                      request_defaults=A_DEFAULTS),
                route("A-Shared", "chat", self.a_shared, backend_model_name="acme/a-shared", models=["shared-id"]),
                route("A-Embed", "embedding", self.a_embed, models=["a-embed"]),
                route("A-TTS", "tts", self.a_tts, models=["a-tts"], health_path=None),
            ],
            name="site-a",
            target=a_target,
        )  # fmt: skip
        self.link_key = self.a.db.create_key(name="site-b-link", owner="site-b")
        self.a.sync_keys()

        # ---- site B: one local model, everything else through A
        self.b_local = factory.fake("vllm", ["acme/b-local", "acme/b-shared"], name="b-local")
        upstream = {
            "name": "site-a",
            "base_url": self.a.router.base_url,
            "api_key_env": "SITE_A_ROUTER_API_KEY",
            "discovery_interval": 1,
            **(upstream_extra or {}),
        }
        self.b = factory.stack(
            [
                route("B-Local", "chat", self.b_local, backend_model_name="acme/b-local", models=["b-chat"]),
                route(
                    "B-Shared", "chat", self.b_local, backend_model_name="acme/b-shared", models=["shared-id"]
                ),
            ],
            name="site-b",
            config={"upstreams": [upstream]},
            env={"SITE_A_ROUTER_API_KEY": self.link_key.token},
        )
        self.a_fakes = [self.a_chat, self.a_shared, self.a_embed, self.a_tts]

    def settle(self, *ids: str) -> None:
        wait_until(lambda: set(ids) <= set(self.b.models()), timeout=SETTLE, what=f"site B to list {ids}")

    def clear(self) -> None:
        for fake in (*self.a_fakes, self.b_local):
            fake.reset()


@pytest.fixture(scope="module")
def _sites(module_factory):
    sites = TwoSites(module_factory)
    sites.settle("a-chat", "acme/a-chat-7b", "a-embed", "a-tts")
    return sites


@pytest.fixture
def sites(_sites):
    _sites.clear()
    yield _sites
    _sites.clear()


def test_upstream_ids_become_models_of_the_downstream_router(sites):
    models = sites.b.models()

    assert {"a-chat", "acme/a-chat-7b", "a-embed", "a-tts", "b-chat", "acme/b-local", "shared-id"} <= set(
        models
    )
    # route name and type are taken from the upstream's own x_router, unchanged
    assert models["a-chat"]["x_router"] == {"route": "A-Chat", "type": "chat"}
    assert models["acme/a-chat-7b"]["x_router"] == {"route": "A-Chat", "type": "chat"}
    assert models["a-embed"]["x_router"] == {"route": "A-Embed", "type": "embedding"}
    assert models["b-chat"]["x_router"] == {"route": "B-Local", "type": "chat"}
    assert models["a-chat"]["max_model_len"] == 32768  # metadata travels through both routers


def test_chat_through_the_upstream(sites):
    r = sites.b.chat("a-chat", "over two hops")

    assert r.status_code == 200, r.text
    seen = sites.a_chat.last(CHAT)
    assert r.content == seen.sent == FakeVLLM.render_completion(seen.json)
    # the alias and the defaults are site A's business: A rewrote the model and added its defaults
    assert seen.json == {"model": "acme/a-chat-7b", "messages": user("over two hops"), **A_DEFAULTS}
    assert not sites.b_local.calls(CHAT)


def test_upstream_is_called_with_the_link_key_never_the_clients(sites):
    # `acme/a-shared` is only reachable through the upstream and no other test asks for it,
    # so site A's usage for that route under the link key is this request alone.
    assert sites.a.db.usage_daily(sites.link_key).get("A-Shared") is None

    assert sites.b.chat("acme/a-shared").status_code == 200

    wait_until(
        lambda: sites.a.db.usage_daily(sites.link_key).get("A-Shared") == (1, 0),
        timeout=5, what="site A to count the request under the link key",
    )  # fmt: skip
    # site A does not know site B's client key: the request can only have passed with the link key
    assert not sites.a.authenticates(sites.b.key)
    assert "authorization" not in sites.a_shared.last(CHAT).headers


def test_stream_through_the_upstream_is_byte_exact(sites):
    sites.a_chat.stream_gap = 0.01
    backend_view = {"model": "acme/a-chat-7b", "messages": user("two hops")}

    plain = sites.b.chat_stream("a-chat", "two hops")
    with_usage = sites.b.chat_stream("a-chat", "two hops", body={"stream_options": {"include_usage": True}})

    assert plain.status == 200 and plain.error is None
    assert plain.raw == b"".join(FakeVLLM.render_stream(backend_view, include_usage=False))
    assert with_usage.raw == b"".join(FakeVLLM.render_stream(backend_view, include_usage=True))


def test_both_sites_count_tokens(sites):
    key_b = sites.b.new_key()
    usage = FakeVLLM.usage({"messages": user("count on both sides")})

    assert sites.b.chat_stream("a-chat", "count on both sides", key=key_b).status == 200

    def tokens(stack, key):
        rows = [r for r in stack.db.usage_hourly(key) if r["route"] == "A-Chat" and r["outcome"] == "ok"]
        return sum(r["completion_tokens"] for r in rows), sum(r["requests"] for r in rows)

    wait_until(
        lambda: tokens(sites.b, key_b) == (usage["completion_tokens"], 1), timeout=6, what="tokens at site B"
    )
    wait_until(
        lambda: tokens(sites.a, sites.link_key)[0] >= usage["completion_tokens"],
        timeout=6,
        what="tokens at site A",
    )
    # usage_daily at B uses the upstream's route name too, so numbers compare across sites
    wait_until(
        lambda: sites.b.db.usage_daily(key_b) == {"A-Chat": (1, 0)}, timeout=5, what="usage_daily at site B"
    )


def test_local_route_wins_over_an_upstream_id(sites):
    r = sites.b.chat("shared-id")

    assert r.status_code == 200
    assert sites.b_local.last(CHAT).json["model"] == "acme/b-shared"
    assert not sites.a_shared.calls(CHAT)
    assert sites.b.models()["shared-id"]["x_router"]["route"] == "B-Shared"


def test_allowlist_uses_the_upstreams_route_names(sites):
    only_a_chat = sites.b.db.create_key(allowed=["A-Chat"])
    only_local = sites.b.db.create_key(allowed=["B-Local"])
    sites.b.sync_keys()

    assert sites.b.chat("a-chat", key=only_a_chat).status_code == 200
    assert sites.b.chat("acme/a-chat-7b", key=only_a_chat).status_code == 200
    assert sites.b.chat("b-chat", key=only_a_chat).status_code == 403
    assert sites.b.chat("a-chat", key=only_local).status_code == 403
    assert (
        sites.b.post("/v1/embeddings", key=only_a_chat, json={"model": "a-embed", "input": "x"}).status_code
        == 403
    )


def test_type_check_works_for_upstream_ids(sites):
    r = sites.b.post("/v1/embeddings", json={"model": "a-chat", "input": "x"})

    assert r.status_code == 400
    error_of(r)
    assert not sites.a_embed.calls("/v1/embeddings")


def test_other_endpoints_through_the_upstream(sites):
    embed = sites.b.post("/v1/embeddings", json={"model": "a-embed", "input": ["x", "y"]})
    speech = sites.b.post("/v1/audio/speech", json={"model": "a-tts", "input": "hello"})
    voices = sites.b.get("/v1/audio/voices", key=None, params={"model": "a-tts"})

    assert embed.status_code == 200 and len(embed.json()["data"]) == 2
    assert speech.status_code == 200 and speech.content == FakeKokoro.audio("hello")
    assert speech.headers["content-type"] == "audio/mpeg"
    assert voices.status_code == 200 and voices.json() == {"voices": FakeKokoro.voices}


def test_backend_error_keeps_its_status_and_message_over_two_hops(sites):
    sites.a_chat.fail(422, {"error": {"message": "context too long", "type": "BadRequestError", "code": 422}})

    r = sites.b.chat("a-chat")

    assert r.status_code == 422
    assert error_of(r)["message"] == "context too long"


def test_upstream_route_names_are_synced_into_the_routes_table(sites):
    rows = {r["name"]: r["type"] for r in sites.b.db.execute("SELECT name, type FROM routes")}

    assert rows["B-Local"] == "chat"
    assert rows.get("A-Chat") == "chat" and rows.get("A-Embed") == "embedding" and rows.get("A-TTS") == "tts"


def test_new_upstream_model_appears_downstream(sites):
    sites.a_chat.add_model("acme/a-new-adapter")

    sites.settle("acme/a-new-adapter")

    assert sites.b.chat("acme/a-new-adapter").status_code == 200
    assert sites.a_chat.last(CHAT).json["model"] == "acme/a-new-adapter"


# ---- own pairs of routers ---------------------------------------------------------


def test_upstream_down_means_503_for_its_ids_and_they_come_back(factory):
    sites = TwoSites(factory)
    sites.settle("a-chat", "acme/a-chat-7b")

    sites.a.router.stop()

    wait_until(
        lambda: "a-chat" not in sites.b.models(), timeout=SETTLE, what="site B to stop listing A's models"
    )
    for model in ("a-chat", "acme/a-chat-7b"):
        r = sites.b.chat(model)
        assert r.status_code == 503 and error_of(r).get("code") == "model_unavailable"
        assert r.headers.get("retry-after") == "30"
    assert sites.b.chat("b-chat").status_code == 200  # local models are unaffected
    rows = {r["name"] for r in sites.b.db.execute("SELECT name FROM routes")}
    assert "A-Chat" in rows  # not deleted while the upstream is merely unreachable

    sites.a.router.start()
    sites.settle("a-chat", "acme/a-chat-7b")
    assert sites.b.chat("a-chat").status_code == 200


def test_include_and_exclude_filter_the_upstream_ids(factory):
    sites = TwoSites(factory, upstream_extra={"include": ["a-*", "shared-*"], "exclude": ["a-emb*"]})
    sites.settle("a-chat", "a-tts")

    holds_for(
        lambda: not {"a-embed", "acme/a-chat-7b", "acme/a-shared"} & set(sites.b.models()),
        seconds=2.5, interval=0.5, what="excluded and not-included ids staying out",
    )  # fmt: skip
    assert sites.b.chat("a-chat").status_code == 200
    assert sites.b.post("/v1/embeddings", json={"model": "a-embed", "input": "x"}).status_code == 404
    assert sites.b.chat("acme/a-chat-7b").status_code == 404


def test_wrong_link_key_makes_upstream_models_fail_cleanly(factory):
    sites = TwoSites(factory)
    sites.settle("a-chat")
    sites.a.db.update_key(sites.link_key, enabled=False)
    wait_until(
        lambda: not sites.a.authenticates(sites.link_key), timeout=5, what="site A to drop the link key"
    )

    r = sites.b.chat("a-chat")

    assert r.status_code in (401, 502, 503)  # never a 200, never a crash
    error_of(r)
    assert sites.b.chat("b-chat").status_code == 200


def test_old_router_as_upstream_has_no_x_router(factory):
    """An upstream that predates C14: its ids are filed under the upstream's name, types are its own business."""
    sites = TwoSites(factory, a_target="legacy")
    sites.settle("a-chat", "acme/a-chat-7b", "a-embed")

    models = sites.b.models()
    assert models["a-chat"]["x_router"]["route"] == "site-a"
    r = sites.b.chat("a-chat", "through the old router")
    assert r.status_code == 200
    assert sites.a_chat.last(CHAT).json["model"] == "acme/a-chat-7b"
    limited = sites.b.new_key(allowed=["site-a"])
    assert sites.b.chat("a-chat", key=limited).status_code == 200
    assert sites.b.chat("b-chat", key=limited).status_code == 403
    # no local type check: the old router answers 400 itself
    wrong = sites.b.post("/v1/embeddings", json={"model": "a-chat", "input": "x"})
    assert wrong.status_code == 400
