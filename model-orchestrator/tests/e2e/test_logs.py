"""Logging: ``ROUTER_LOG_LEVEL`` stays valid; the request log line of design 7.7."""

from __future__ import annotations

import json

import pytest

from tests.harness.stack import route
from tests.harness.util import wait_until

CHAT = "/v1/chat/completions"


def solo(factory, **env):
    backend = factory.fake("vllm", ["acme/solo"])
    stack = factory.stack(
        [route("Solo", "chat", backend, backend_model_name="acme/solo", models=["solo"])],
        env=env,
        name="logs",
    )
    return stack, backend


@pytest.mark.parametrize("level", ["DEBUG", "warning", "ERROR"])
def test_log_level_env_is_accepted(factory, level):
    stack, _ = solo(factory, ROUTER_LOG_LEVEL=level)

    assert stack.chat("solo").status_code == 200
    assert stack.chat("no-such-model").status_code == 404


def test_debug_logging_does_not_leak_the_api_key(factory):
    stack, _ = solo(factory, ROUTER_LOG_LEVEL="DEBUG")

    stack.chat("solo")
    stack.chat_stream("solo")

    assert stack.key.token not in stack.router.log()


def json_lines(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def flat(obj) -> list:
    if isinstance(obj, dict):
        return [v for value in obj.values() for v in flat(value)]
    if isinstance(obj, list):
        return [v for value in obj for v in flat(value)]
    return [obj]


@pytest.mark.feature("request_log")
def test_one_json_log_line_per_request(factory):
    stack, _ = solo(factory, ROUTER_LOG_FORMAT="json")

    stack.chat("solo", "a-prompt-that-must-not-be-logged-91c2", headers={"X-Request-ID": "log-rid-ok"})
    stack.chat("no-such-model", headers={"X-Request-ID": "log-rid-404"})
    assert stack.chat_stream("solo", headers={"X-Request-ID": "log-rid-stream"}).status == 200

    def lines_for(rid: str) -> list[dict]:
        return [line for line in json_lines(stack.router.log()) if rid in flat(line)]

    ok = wait_until(lambda: lines_for("log-rid-ok"), timeout=5, what="the request log line")
    assert len(ok) == 1, f"expected one line per request, got {ok}"
    values = flat(ok[0])
    assert 200 in values and "ok" in values  # status and outcome
    assert "Solo" in values and "solo" in values  # route and the model id as sent
    assert stack.key.name in values and stack.key.owner in values
    usage_tokens = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
    assert usage_tokens, "no numeric fields (status, duration, tokens) in the request line"

    missing = wait_until(lambda: lines_for("log-rid-404"), timeout=5, what="the 404 request log line")
    assert 404 in flat(missing[0])
    stream = wait_until(lambda: lines_for("log-rid-stream"), timeout=5, what="the stream's request log line")
    assert len(stream) == 1 and True in flat(stream[0])  # the stream flag

    log = stack.router.log()
    assert "a-prompt-that-must-not-be-logged-91c2" not in log
    assert stack.key.token not in log
