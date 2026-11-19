"""``python -m model_orchestrator check-config <file>`` (design 7.2). New router only."""

from __future__ import annotations

import subprocess

import pytest
import yaml

from tests.harness.router import ROUTER_DIR, base_command, base_environment

pytestmark = pytest.mark.feature("check_config")

GOOD = {
    "name": "Solo",
    "type": "chat",
    "backend": "http://127.0.0.1:9",
    "endpoint": "/v1/chat/completions",
    "backend_model_name": "acme/solo",
    "models": ["solo", "solo-latest"],
    "owned_by": "acme",
    "proxy": "socks5h://127.0.0.1:1080",
    "health_path": None,
    "backend_api_key_env": "E2E_UNSET_BACKEND_KEY",
    "request_defaults": {"skip_special_tokens": False, "chat_template_kwargs": {"enable_thinking": True}},
}
OTHER = {"name": "Other", "type": "embedding", "backend": ["http://127.0.0.1:9", "http://127.0.0.1:10"],
         "endpoint": "/v1/embeddings", "models": ["other"]}  # fmt: skip


def check(tmp_path, document, *extra_args: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "config.yaml"
    path.write_text(document if isinstance(document, str) else yaml.safe_dump(document, sort_keys=False))
    return subprocess.run(
        [*base_command("new"), "check-config", str(path), *extra_args],
        cwd=ROUTER_DIR, env=base_environment(), capture_output=True, text=True, timeout=30,
    )  # fmt: skip


def without(route: dict, key: str) -> dict:
    return {k: v for k, v in route.items() if k != key}


def test_valid_config_passes(tmp_path):
    done = check(tmp_path, {"server": {"host": "0.0.0.0", "port": 8090}, "routes": [GOOD, OTHER]})

    assert done.returncode == 0, done.stdout + done.stderr


def test_the_new_optional_keys_are_accepted(tmp_path):
    document = {
        "routes": [{**GOOD, "timeout_s": 900, "usage_from_stream": False}],
        "upstreams": [
            {
                "name": "site-a", "base_url": "http://127.0.0.1:9", "api_key_env": "E2E_UNSET_LINK_KEY",
                "health_path": "/livez", "include": ["*"], "exclude": [], "discovery_interval": 60,
                "proxy": None, "tls_verify": True, "ca_file": None,
            }
        ],
    }  # fmt: skip

    done = check(tmp_path, document)

    assert done.returncode == 0, done.stdout + done.stderr


def test_the_example_config_in_the_repository_passes():
    done = subprocess.run(
        [*base_command("new"), "check-config", str(ROUTER_DIR / "config.example.yaml")],
        cwd=ROUTER_DIR, env=base_environment(), capture_output=True, text=True, timeout=30,
    )  # fmt: skip

    assert done.returncode == 0, done.stdout + done.stderr


def test_it_needs_no_database_and_starts_nothing(tmp_path):
    """The command only reads the file: no ROUTER_DATABASE_URL is set in its environment here."""
    done = check(tmp_path, {"routes": [OTHER]})

    assert done.returncode == 0, done.stdout + done.stderr


INVALID = {
    "duplicate-alias": ([GOOD, {**OTHER, "models": ["solo"]}], ["solo"]),
    "duplicate-route-name": ([GOOD, {**OTHER, "name": "Solo"}], ["Solo"]),
    "unknown-key": ([{**GOOD, "request_default": {"x": 1}}], ["Solo", "request_default"]),
    "unknown-type": ([{**GOOD, "type": "chatbot"}], ["Solo", "chatbot"]),
    "missing-name": ([without(OTHER, "name")], ["name"]),
    "missing-type": ([without(GOOD, "type")], ["Solo", "type"]),
    "missing-backend": ([without(GOOD, "backend")], ["Solo", "backend"]),
    "missing-endpoint": ([without(GOOD, "endpoint")], ["Solo", "endpoint"]),
}


@pytest.mark.parametrize("problem", INVALID)
def test_invalid_config_fails_and_names_the_route_and_field(tmp_path, problem):
    routes, mentions = INVALID[problem]

    done = check(tmp_path, {"routes": routes})

    output = done.stdout + done.stderr
    assert done.returncode != 0, f"check-config accepted a config with: {problem}"
    for word in mentions:
        assert word in output, f"{word!r} is not mentioned in: {output!r}"
    assert "Traceback" not in output, "a validation error should be a message, not a stack trace"


@pytest.mark.parametrize(
    "text", ["routes:\n  - name: [unclosed\n", "- a\n- list\n"], ids=["broken-yaml", "not-a-mapping"]
)
def test_unreadable_config_fails(tmp_path, text):
    done = check(tmp_path, text)

    assert done.returncode != 0
    assert "Traceback" not in done.stdout + done.stderr


def test_missing_file_fails(tmp_path):
    done = subprocess.run(
        [*base_command("new"), "check-config", str(tmp_path / "nope.yaml")],
        cwd=ROUTER_DIR, env=base_environment(), capture_output=True, text=True, timeout=30,
    )  # fmt: skip

    assert done.returncode != 0
