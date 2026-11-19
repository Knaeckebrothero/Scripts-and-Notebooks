"""The model table: precedence, remembered ids, upstream ids (design 7.3, 7.9, contract section 5)."""

from __future__ import annotations

from pathlib import Path

from model_orchestrator.config import load_config, parse_config
from model_orchestrator.registry import Registry, model_object, targets_from_config

from .support import listing, route_target, upstream_target

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_aliases_route_and_rewrite_to_the_backend_model_name() -> None:
    target = route_target(aliases=("gpt-4o-mini", "gemma"), backend_model_name="org/gemma-4")
    table = Registry([target]).table
    assert list(table.entries) == ["gpt-4o-mini", "gemma"]
    entry = table.get("gpt-4o-mini")
    assert entry is not None
    assert (entry.route_name, entry.route_type, entry.rewrite_to, entry.source) == (
        "Chat",
        "chat",
        "org/gemma-4",
        "alias",
    )
    assert entry.listed


def test_an_alias_without_backend_model_name_is_sent_as_is() -> None:
    entry = Registry([route_target(aliases=("plain",))]).table.get("plain")
    assert entry is not None and entry.rewrite_to == "plain"


def test_lookup_of_unknown_or_non_string_ids() -> None:
    table = Registry([route_target(aliases=("a",))]).table
    for bad in (None, "", "nope", 5, ["a"], {"a": 1}):
        assert table.get(bad) is None


def test_discovered_ids_are_forwarded_as_sent() -> None:
    target = route_target(aliases=("alias",), backend_model_name="org/model")
    registry = Registry([target])
    assert registry.apply_listing(target, listing("org/model", "org/model-lora", max_model_len=4096))
    table = registry.table
    assert list(table.entries) == ["alias", "org/model", "org/model-lora"]
    discovered = table.get("org/model-lora")
    assert discovered is not None
    assert (discovered.rewrite_to, discovered.source, discovered.route_name) == (None, "discovered", "Chat")
    # The alias is enriched with the backend's entry for the model it points at.
    alias = table.get("alias")
    assert alias is not None and alias.meta is not None and alias.meta["max_model_len"] == 4096


def test_an_alias_beats_a_discovered_id_whatever_the_route_order() -> None:
    first = route_target("First", aliases=("a",))
    second = route_target(
        "Second", aliases=("shared-id",), backend_model_name="other", backends=("http://b.invalid:1",)
    )
    registry = Registry([first, second])
    registry.apply_listing(first, listing("shared-id"))
    entry = registry.table.get("shared-id")
    assert entry is not None and entry.route_name == "Second" and entry.source == "alias"


def test_among_discovered_ids_the_first_route_in_config_order_wins() -> None:
    """Three routes on one backend (as in the live configs) all discover the same id."""
    chat = route_target("Chat-Pool", aliases=("gemma",))
    tutor = route_target("Tutor", aliases=("gpt-4o-mini",))
    vision = route_target("Vision-Pool", aliases=("gpt-4o",), route_type="vision")
    registry = Registry([chat, tutor, vision])
    for target in (vision, tutor, chat):
        registry.apply_listing(target, listing("org/gemma-4"))
    entry = registry.table.get("org/gemma-4")
    assert entry is not None and entry.route_name == "Chat-Pool"


def test_discovered_ids_are_remembered_but_marked_unlisted() -> None:
    target = route_target(aliases=("alias",))
    registry = Registry([target])
    registry.apply_listing(target, listing("org/a", "org/b"))
    registry.apply_listing(target, listing("org/b"))
    gone = registry.table.get("org/a")
    assert gone is not None, "an id seen before stays known (503, not 404)"
    assert not gone.listed
    kept = registry.table.get("org/b")
    alias = registry.table.get("alias")
    assert kept is not None and kept.listed
    assert alias is not None and alias.listed, "aliases never depend on discovery"


def test_a_failed_discovery_changes_nothing() -> None:
    target = route_target()
    registry = Registry([target])
    registry.apply_listing(target, listing("org/a"))
    before = registry.table
    registry.note_no_listing(target, "ConnectError", answered=False)
    registry.note_no_listing(target, "HTTP 404", answered=True)
    assert registry.table is before
    entry = registry.table.get("org/a")
    assert entry is not None and entry.listed
    state = registry.discovery_state(target)
    assert state.last_error == "HTTP 404" and state.failures == 0


def test_unanswered_discoveries_are_counted_and_reset() -> None:
    target = route_target()
    registry = Registry([target])
    registry.note_no_listing(target, "ConnectError", answered=False)
    registry.note_no_listing(target, "ConnectError", answered=False)
    assert registry.discovery_state(target).failures == 2
    registry.apply_listing(target, [])
    assert registry.discovery_state(target).failures == 0


def test_apply_listing_reports_whether_routing_changed() -> None:
    target = route_target()
    registry = Registry([target])
    assert registry.apply_listing(target, listing("a")), "a new id"
    assert not registry.apply_listing(target, listing("a"))
    assert registry.apply_listing(target, listing("a", "b")), "another id"
    assert registry.apply_listing(target, listing("a")), "an id is no longer listed"
    assert registry.apply_listing(target, []), "nothing is listed any more"
    assert not registry.apply_listing(target, [])


def test_metadata_that_changes_on_every_call_is_taken_over_silently() -> None:
    """vLLM reports a new ``created`` and a new permission id each time it is asked."""

    def vllm(created: int) -> list[dict]:
        permission = [{"id": f"modelperm-{created}", "object": "model_permission", "created": created}]
        return [{"id": "org/model", "object": "model", "created": created, "permission": permission}]

    target = route_target(aliases=("alias",), backend_model_name="org/model")
    registry = Registry([target])
    assert registry.apply_listing(target, vllm(1000))
    before = registry.table
    for created in (1001, 1002, 1003):
        assert not registry.apply_listing(target, vllm(created)), "not a change worth announcing"
    assert registry.table is not before, "but /v1/models shows the current values"
    for model_id in ("alias", "org/model"):
        entry = registry.table.get(model_id)
        assert entry is not None and model_object(entry)["created"] == 1003
        assert model_object(entry)["permission"][0]["id"] == "modelperm-1003"


def test_a_changed_x_router_object_is_a_routing_change() -> None:
    upstream = upstream_target()

    def site(route: str, type_: str = "chat", created: int = 1) -> list[dict]:
        return [{"id": "gemma", "created": created, "x_router": {"route": route, "type": type_}}]

    registry = Registry([upstream])
    assert registry.apply_listing(upstream, site("Gemma-Pool"))
    assert not registry.apply_listing(upstream, site("Gemma-Pool", created=2))
    assert registry.apply_listing(upstream, site("Gemma-Renamed")), "the route name drives allowlists"
    assert registry.apply_listing(upstream, site("Gemma-Renamed", "vision")), (
        "the type drives the endpoint check"
    )
    assert registry.apply_listing(upstream, [{"id": "gemma"}]), "x_router went away"
    entry = registry.table.get("gemma")
    assert entry is not None and entry.route_name == "site-a" and entry.route_type is None


def test_junk_in_a_listing_is_skipped() -> None:
    target = route_target()
    registry = Registry([target])
    registry.apply_listing(target, [{"id": "ok"}, {"id": ""}, {"id": 5}, {"object": "model"}, "text", None])
    assert list(registry.table.entries) == ["ok"]


def test_reload_keeps_discovered_ids_for_routes_that_stay() -> None:
    old = route_target("Stays", aliases=("a",))
    removed = route_target("Removed", aliases=("b",), backends=("http://b.invalid:1",))
    registry = Registry([old, removed])
    registry.apply_listing(old, listing("org/stays"))
    registry.apply_listing(removed, listing("org/removed"))
    new = route_target("Stays", aliases=("a", "a2"))
    registry.replace_targets([new])
    assert list(registry.table.entries) == ["a", "a2", "org/stays"]
    assert registry.table.get("org/removed") is None
    assert registry.is_current(new) and not registry.is_current(old)
    # A listing that arrives late for the replaced target object is ignored.
    assert not registry.apply_listing(old, listing("org/late"))
    assert registry.table.get("org/late") is None


# -- /v1/models entries -------------------------------------------------------------------


def test_model_object_stub_for_backends_without_a_models_endpoint() -> None:
    entry = Registry([route_target("Kokoro", aliases=("tts-1",), route_type="tts", owned_by=None)]).table.get(
        "tts-1"
    )
    assert entry is not None
    assert model_object(entry) == {
        "id": "tts-1",
        "object": "model",
        "created": 1704067200,
        "owned_by": "local",
        "permission": [],
        "root": "tts-1",
        "parent": None,
        "x_router": {"route": "Kokoro", "type": "tts"},
    }


def test_model_object_passes_backend_metadata_through() -> None:
    target = route_target(aliases=("alias",), backend_model_name="org/model", owned_by="google")
    registry = Registry([target])
    registry.apply_listing(
        target,
        [
            {
                "id": "org/model",
                "object": "model",
                "created": 5,
                "owned_by": "vllm",
                "max_model_len": 131072,
                "root": "org/model",
            }
        ],
    )
    alias = registry.table.get("alias")
    discovered = registry.table.get("org/model")
    assert alias is not None and discovered is not None
    assert model_object(alias) == {
        "id": "alias",
        "object": "model",
        "created": 5,
        "owned_by": "google",
        "max_model_len": 131072,
        "root": "org/model",
        "x_router": {"route": "Chat", "type": "chat"},
    }
    assert model_object(discovered)["id"] == "org/model"
    assert model_object(discovered)["x_router"] == {"route": "Chat", "type": "chat"}
    assert discovered.meta is not None and "x_router" not in discovered.meta, (
        "the remembered entry is not modified"
    )


# -- upstream routers (design 7.9) ---------------------------------------------------------------


def upstream_listing() -> list[dict]:
    return [
        {
            "id": "gemma-4-moe",
            "object": "model",
            "owned_by": "google",
            "x_router": {"route": "Gemma4-MoE-Chat-Pool", "type": "chat"},
        },
        {
            "id": "qwen3-embedding-8b",
            "object": "model",
            "x_router": {"route": "Qwen3-Embedding-8B", "type": "embedding"},
        },
        {"id": "kokoro", "object": "model", "x_router": {"route": "Kokoro-TTS", "type": "tts"}},
    ]


def test_upstream_ids_take_route_name_and_type_from_x_router() -> None:
    upstream = upstream_target()
    registry = Registry([upstream])
    registry.apply_listing(upstream, upstream_listing())
    entry = registry.table.get("gemma-4-moe")
    assert entry is not None
    assert (entry.route_name, entry.route_type, entry.rewrite_to, entry.source) == (
        "Gemma4-MoE-Chat-Pool",
        "chat",
        None,
        "upstream",
    )
    assert model_object(entry)["x_router"] == {"route": "Gemma4-MoE-Chat-Pool", "type": "chat"}
    assert model_object(entry)["owned_by"] == "google"
    assert registry.table.route_names() == {
        "Gemma4-MoE-Chat-Pool": "chat",
        "Qwen3-Embedding-8B": "embedding",
        "Kokoro-TTS": "tts",
    }
    first_tts = registry.table.first_of_type("tts")
    assert first_tts is not None and first_tts.model_id == "kokoro"


def test_an_older_upstream_without_x_router_gets_the_upstream_name_and_no_type() -> None:
    upstream = upstream_target("legacy-site")
    registry = Registry([upstream])
    registry.apply_listing(upstream, listing("some-model"))
    entry = registry.table.get("some-model")
    assert entry is not None and entry.route_name == "legacy-site" and entry.route_type is None
    assert model_object(entry)["x_router"] == {"route": "legacy-site", "type": None}
    assert registry.table.route_names() == {"legacy-site": "upstream"}


def test_an_unknown_type_from_a_newer_upstream_means_no_type_check() -> None:
    upstream = upstream_target()
    registry = Registry([upstream])
    registry.apply_listing(
        upstream,
        [{"id": "img", "x_router": {"route": "Images", "type": "image"}}, {"id": "odd", "x_router": "nope"}],
    )
    img, odd = registry.table.get("img"), registry.table.get("odd")
    assert img is not None and img.route_name == "Images" and img.route_type is None
    assert odd is not None and odd.route_name == "site-a" and odd.route_type is None


def test_local_routes_win_over_upstream_ids() -> None:
    local = route_target("Local-Chat", aliases=("gemma-4-moe",))
    upstream = upstream_target()
    registry = Registry([local, upstream])
    registry.apply_listing(upstream, upstream_listing())
    registry.apply_listing(local, listing("kokoro"))
    alias, discovered = registry.table.get("gemma-4-moe"), registry.table.get("kokoro")
    assert alias is not None and alias.route_name == "Local-Chat" and alias.source == "alias"
    assert (
        discovered is not None and discovered.route_name == "Local-Chat" and discovered.source == "discovered"
    )
    remote = registry.table.get("qwen3-embedding-8b")
    assert remote is not None and remote.source == "upstream"


def test_between_upstreams_the_first_in_config_order_wins() -> None:
    first, second = (
        upstream_target("site-a"),
        upstream_target("site-b", backends=("http://site-b-router.invalid:8091",)),
    )
    registry = Registry([first, second])
    registry.apply_listing(second, [{"id": "shared"}, {"id": "only-b"}])
    registry.apply_listing(first, [{"id": "shared"}])
    shared, only_b = registry.table.get("shared"), registry.table.get("only-b")
    assert shared is not None and shared.target is first
    assert only_b is not None and only_b.target is second


def test_include_and_exclude_globs() -> None:
    upstream = upstream_target(include=("gemma-*", "kokoro"), exclude=("*-vision",))
    registry = Registry([upstream])
    registry.apply_listing(
        upstream, listing("gemma-4-moe", "gemma-4-moe-vision", "kokoro", "whisper-1", "Gemma-upper")
    )
    assert list(registry.table.entries) == ["gemma-4-moe", "kokoro"]


def test_an_id_the_upstream_stops_listing_stays_known_but_unlisted() -> None:
    upstream = upstream_target()
    registry = Registry([upstream])
    registry.apply_listing(upstream, upstream_listing())
    registry.apply_listing(upstream, upstream_listing()[:1])
    gone = registry.table.get("kokoro")
    assert gone is not None and not gone.listed
    assert "Kokoro-TTS" in registry.table.route_names(), "its route name is not forgotten either"


# -- from config -------------------------------------------------------------------------------------


def test_targets_from_config_resolves_keys_and_keeps_order() -> None:
    config = parse_config(
        {
            "routes": [
                {
                    "name": "Hosted",
                    "type": "chat",
                    "backend": ["http://a.invalid:1", "http://b.invalid:2"],
                    "endpoint": "/v1/chat/completions",
                    "backend_api_key_env": "HOSTED_KEY",
                    "proxy": "socks5h://127.0.0.1:1080",
                    "models": ["m", "m"],
                    "timeout_s": 30,
                    "usage_from_stream": False,
                    "health_path": None,
                }
            ],
            "upstreams": [
                {
                    "name": "site-a",
                    "base_url": "https://site-a.invalid/",
                    "api_key_env": "UP_KEY",
                    "tls_verify": False,
                    "exclude": ["x*"],
                }
            ],
        }
    )
    route, upstream = targets_from_config(
        config, {"HOSTED_KEY": "secret", "UP_KEY": ""}, discovery_interval=300.0
    )
    assert (route.kind, route.name, route.backends) == (
        "route",
        "Hosted",
        ("http://a.invalid:1", "http://b.invalid:2"),
    )
    assert route.api_key == "secret" and route.client_key.proxy == "socks5h://127.0.0.1:1080"
    assert route.aliases == ("m",) and route.timeout_s == 30.0 and not route.usage_from_stream
    assert route.health_path is None and route.discovery_interval == 300.0
    assert (upstream.kind, upstream.backends) == ("upstream", ("https://site-a.invalid",))
    assert upstream.api_key is None, "an empty variable is no key"
    assert upstream.client_key.verify is False and upstream.exclude == ("x*",)
    assert (
        upstream.health_path == "/livez"
        and upstream.discovery_interval == 60.0
        and upstream.usage_from_stream
    )


def test_table_of_a_live_config() -> None:
    config = load_config(str(FIXTURES / "live_config_site_a.yaml"))
    registry = Registry(targets_from_config(config, {}, discovery_interval=300.0))
    table = registry.table
    assert len(table.entries) == 17
    gpt4o_mini = table.get("gpt-4o-mini")
    assert gpt4o_mini is not None and gpt4o_mini.route_name == "Gemma4-MoE-Tutor"
    assert gpt4o_mini.rewrite_to == "RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic"
    assert gpt4o_mini.target.request_defaults["chat_template_kwargs"] == {"enable_thinking": False}
    whisper = table.get("whisper-1")
    assert whisper is not None and whisper.route_type == "stt" and whisper.rewrite_to == "whisper-1"
    first_stt, first_tts = table.first_of_type("stt"), table.first_of_type("tts")
    assert first_stt is not None and first_stt.model_id == "whisper-large-v3"
    assert first_tts is not None and first_tts.model_id == "kokoro"
    assert len(table.route_names()) == 9
