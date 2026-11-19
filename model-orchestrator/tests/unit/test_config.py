"""Config validation (design 7.2), including the sanitised copies of both live configs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from model_orchestrator.config import (
    ConfigError,
    config_warnings,
    load_config,
    parse_config,
    parse_yaml,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def route(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Chat",
        "type": "chat",
        "backend": "http://backend-a.invalid:8000",
        "endpoint": "/v1/chat/completions",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not ...}


def errors_of(data: Any) -> list[str]:
    with pytest.raises(ConfigError) as caught:
        parse_config(data)
    return caught.value.messages


# -- the two live configs ---------------------------------------------------------


@pytest.mark.parametrize("name", ["live_config_site_a.yaml", "live_config_site_b.yaml"])
def test_live_config_loads_unchanged(name: str) -> None:
    config = load_config(str(FIXTURES / name))
    assert config.routes, "a live config has routes"
    assert config_warnings(config, {}) == []


def test_live_config_site_a_details() -> None:
    config = load_config(str(FIXTURES / "live_config_site_a.yaml"))
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8091
    assert len(config.routes) == 9
    by_name = {r.name: r for r in config.routes}
    pool = by_name["Gemma4-MoE-Chat-Pool"]
    assert pool.backend == ["http://127.0.0.1:8088"]
    assert pool.request_defaults == {
        "skip_special_tokens": False,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    assert pool.health_path == "/health"
    # A single URL becomes a pool of one; explicit null switches the probe off.
    assert by_name["Qwen3-Embedding-8B"].backend == ["http://127.0.0.1:8081"]
    assert by_name["Whisper-large-v3"].health_path is None
    assert by_name["Kokoro-TTS"].health_path is None
    # Three routes share one backend and one backend model name; that is allowed.
    shared = [r for r in config.routes if r.backend_model_name == "RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic"]
    assert len(shared) == 3


def test_live_config_site_b_details() -> None:
    config = load_config(str(FIXTURES / "live_config_site_b.yaml"))
    assert config.server.host == "0.0.0.0"
    assert len(config.routes) == 11
    assert {r.type for r in config.routes} == {"embedding", "reranker", "chat", "vision", "stt", "tts"}
    assert config.upstreams == []


# -- what stays valid --------------------------------------------------------------


def test_minimal_route_and_defaults() -> None:
    config = parse_config({"routes": [route()]})
    r = config.routes[0]
    assert r.backend == ["http://backend-a.invalid:8000"]
    assert r.models == []
    assert r.health_path == "/health"
    assert r.request_defaults == {}
    assert r.usage_from_stream is True
    assert r.timeout_s is None
    assert r.backend_model_name is None and r.owned_by is None and r.proxy is None


def test_empty_document_is_an_empty_config() -> None:
    assert parse_config(None).routes == []
    assert parse_config({}).routes == []
    assert parse_config({"routes": None, "server": None}).routes == []


def test_backend_accepts_a_url_or_a_list() -> None:
    config = parse_config({"routes": [route(backend=["http://a.invalid:1", "http://b.invalid:2"])]})
    assert config.routes[0].backend == ["http://a.invalid:1", "http://b.invalid:2"]


@pytest.mark.parametrize("value", [None, ""])
def test_health_path_null_and_empty_both_mean_no_probe(value: str | None) -> None:
    assert parse_config({"routes": [route(health_path=value)]}).routes[0].health_path is None


def test_health_path_custom() -> None:
    assert parse_config({"routes": [route(health_path="/ping")]}).routes[0].health_path == "/ping"


def test_null_models_and_request_defaults_are_empty() -> None:
    r = parse_config({"routes": [route(models=None, request_defaults=None)]}).routes[0]
    assert r.models == [] and r.request_defaults == {}


def test_new_optional_route_keys() -> None:
    r = parse_config({"routes": [route(timeout_s=30, usage_from_stream=False)]}).routes[0]
    assert r.timeout_s == 30.0 and r.usage_from_stream is False


@pytest.mark.parametrize(
    "proxy", ["socks5h://127.0.0.1:1080", "socks5://127.0.0.1:1080", "http://proxy.invalid:3128"]
)
def test_proxy_schemes(proxy: str) -> None:
    assert parse_config({"routes": [route(proxy=proxy)]}).routes[0].proxy == proxy


def test_same_alias_twice_in_one_route_is_harmless() -> None:
    config = parse_config({"routes": [route(models=["a", "a", "b"])]})
    assert config.routes[0].models == ["a", "a", "b"]


def test_alias_may_equal_its_own_backend_model_name() -> None:
    parse_config(
        {"routes": [route(backend_model_name="whisper-1", models=["whisper-large-v3", "whisper-1"])]}
    )


# -- errors: each names the route and the field ---------------------------------------


@pytest.mark.parametrize("missing", ["name", "type", "backend", "endpoint"])
def test_missing_required_key(missing: str) -> None:
    messages = errors_of({"routes": [route(**{missing: ...})]})
    assert len(messages) == 1
    assert f"missing required key '{missing}'" in messages[0]
    assert "routes[0]" in messages[0]
    if missing != "name":
        assert "route 'Chat'" in messages[0]


def test_unknown_key_is_an_error_with_a_hint() -> None:
    messages = errors_of({"routes": [route(request_default={"skip_special_tokens": False})]})
    assert messages == [
        "route 'Chat' (routes[0]): unknown key 'request_default' (did you mean 'request_defaults'?)"
    ]


def test_unknown_top_level_and_server_keys() -> None:
    assert "unknown key 'route'" in errors_of({"route": []})[0]
    assert errors_of({"server": {"hosts": "x"}}) == ["server: unknown key 'hosts' (did you mean 'host'?)"]


def test_unknown_type() -> None:
    messages = errors_of({"routes": [route(type="chatt")]})
    assert len(messages) == 1
    assert messages[0].startswith(
        "route 'Chat' (routes[0]): unknown type 'chatt'; expected one of chat, vision"
    )


def test_duplicate_route_name() -> None:
    messages = errors_of({"routes": [route(), route(models=["x"])]})
    assert messages == ["route 'Chat' (routes[1]): duplicate route name, already used by routes[0]"]


def test_duplicate_alias_across_routes_is_an_error() -> None:
    """C11: two routes sharing an alias used to let the later one win silently."""
    messages = errors_of(
        {"routes": [route(name="A", models=["gpt-4o", "a"]), route(name="B", models=["b", "gpt-4o"])]}
    )
    assert messages == ["route 'B' (routes[1]): models: alias 'gpt-4o' is already defined by route 'A'"]


def test_every_problem_is_reported_in_one_run() -> None:
    messages = errors_of(
        {
            "routes": [
                route(name="A", models=["x"], colour="blue"),
                route(name="B", models=["x"], type="nope"),
                {"name": "C", "type": "chat"},
            ]
        }
    )
    text = "\n".join(messages)
    assert "route 'A' (routes[0]): unknown key 'colour'" in text
    assert "route 'B' (routes[1]): unknown type 'nope'" in text
    assert "route 'C' (routes[2]): missing required key 'backend'" in text
    assert "route 'C' (routes[2]): missing required key 'endpoint'" in text
    assert "route 'B' (routes[1]): models: alias 'x' is already defined by route 'A'" in text


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"backend": []}, "backend"),
        ({"backend": ""}, "backend"),
        ({"backend": ["http://a.invalid:1", ""]}, "backend"),
        ({"backend": "a.invalid:8000"}, "http:// or https://"),
        ({"backend": ["http://a.invalid:1", "http://a.invalid:1"]}, "more than once"),
        ({"endpoint": "v1/chat/completions"}, "endpoint must start with '/'"),
        ({"health_path": "health"}, "health_path must start with '/'"),
        ({"proxy": "ftp://proxy.invalid"}, "proxy must start with"),
        ({"timeout_s": 0}, "timeout_s must be greater than 0"),
        ({"models": ["ok", ""]}, "models"),
        ({"models": "one"}, "models"),
        ({"name": ""}, "name must not be empty"),
        ({"name": 12}, "name"),
        ({"usage_from_stream": "yes"}, "usage_from_stream"),
        ({"request_defaults": ["a"]}, "request_defaults"),
    ],
)
def test_bad_values(overrides: dict[str, Any], fragment: str) -> None:
    messages = errors_of({"routes": [route(**overrides)]})
    assert any(fragment in message for message in messages), messages
    assert all("routes[0]" in message for message in messages)


def test_top_level_must_be_a_mapping() -> None:
    assert "top level must be a mapping" in errors_of(["routes"])[0]


def test_yaml_duplicate_key_is_refused() -> None:
    text = "routes:\n  - name: A\n    type: chat\n    type: vision\n"
    with pytest.raises(ConfigError) as caught:
        parse_yaml(text)
    assert "duplicate key 'type'" in caught.value.messages[0]


def test_yaml_syntax_error_is_a_config_error() -> None:
    with pytest.raises(ConfigError) as caught:
        parse_yaml("routes: [")
    assert caught.value.messages[0].startswith("not valid YAML")


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config(str(tmp_path / "nope.yaml"))
    assert "cannot read" in caught.value.messages[0]


# -- warnings ------------------------------------------------------------------------


def test_unset_backend_key_env_is_a_warning_not_an_error() -> None:
    config = parse_config({"routes": [route(backend_api_key_env="HOSTED_KEY")]})
    assert "HOSTED_KEY" in config_warnings(config, {})[0]
    assert "HOSTED_KEY" in config_warnings(config, {"HOSTED_KEY": ""})[0]
    assert config_warnings(config, {"HOSTED_KEY": "secret"}) == []


def test_empty_config_warns() -> None:
    assert "no routes" in config_warnings(parse_config({}), {})[0]


# -- upstreams (design 7.9) -------------------------------------------------------------


def test_upstream_defaults() -> None:
    config = parse_config(
        {"upstreams": [{"name": "site-a", "base_url": "http://site-a-router.invalid:8091/"}]}
    )
    upstream = config.upstreams[0]
    assert upstream.base_url == "http://site-a-router.invalid:8091"
    assert upstream.health_path == "/livez"
    assert upstream.include == ["*"] and upstream.exclude == []
    assert upstream.discovery_interval == 60.0
    assert upstream.proxy is None and upstream.tls_verify is True and upstream.ca_file is None
    assert upstream.api_key_env is None


def test_upstream_full_form_from_the_design() -> None:
    config = parse_config(
        {
            "upstreams": [
                {
                    "name": "site-a",
                    "base_url": "http://site-a-router.invalid:8091",
                    "api_key_env": "SITE_A_ROUTER_API_KEY",
                    "health_path": "/livez",
                    "include": ["*"],
                    "exclude": [],
                    "discovery_interval": 60,
                    "proxy": None,
                    "tls_verify": True,
                    "ca_file": None,
                }
            ]
        }
    )
    assert config.upstreams[0].api_key_env == "SITE_A_ROUTER_API_KEY"
    assert "SITE_A_ROUTER_API_KEY" in config_warnings(config, {})[0]


def test_upstream_errors() -> None:
    assert "upstream 'u' (upstreams[0]): missing required key 'base_url'" in errors_of(
        {"upstreams": [{"name": "u"}]}
    )
    assert (
        "unknown key 'url'"
        in errors_of({"upstreams": [{"name": "u", "base_url": "http://x.invalid", "url": 1}]})[0]
    )
    both = {"name": "u", "base_url": "http://x.invalid"}
    assert "duplicate upstream name" in errors_of({"upstreams": [both, both]})[0]
    assert (
        "name is already used by a route" in errors_of({"routes": [route(name="u")], "upstreams": [both]})[0]
    )
    assert "discovery_interval" in errors_of({"upstreams": [{**both, "discovery_interval": 0}]})[0]
