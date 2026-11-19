"""Prometheus metrics on ``ROUTER_METRICS_PORT`` (design 7.7). New router only.

Metric names are the builder's choice, so these tests only rely on the
exposition format and on label *values* the design fixes (route names,
outcomes). Assumed path: ``GET /metrics`` on the internal port.
"""

from __future__ import annotations

import re

import httpx
import pytest

from tests.harness import world as w
from tests.harness.util import wait_until

pytestmark = pytest.mark.feature("prometheus")
SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(\S+)(\s+\d+)?$")


def scrape(stack) -> httpx.Response:
    return httpx.get(f"{stack.router.metrics_url}/metrics", timeout=5, trust_env=False)


def samples(text: str) -> list[tuple[str, str, float]]:
    """(name, label block, value) of every sample line."""
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = SAMPLE.match(line)
        assert match, f"not a Prometheus sample line: {line!r}"
        out.append((match.group(1), match.group(2) or "", float(match.group(3))))
    return out


def total(text: str, *needles: str) -> float:
    """Sum of all counter-like samples whose label block contains every needle."""
    return sum(
        value for name, labels, value in samples(text)
        if all(n in labels for n in needles) and not name.endswith(("_bucket", "_sum", "_created"))
    )  # fmt: skip


def test_metrics_endpoint_speaks_the_prometheus_text_format(stack, world):
    r = scrape(stack)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith(("text/plain", "application/openmetrics-text"))
    assert "# TYPE " in r.text and "# HELP " in r.text
    assert len(samples(r.text)) > 5


def test_metrics_need_no_key_and_are_not_on_the_api_port(stack, world):
    assert scrape(stack).status_code == 200  # no Authorization sent
    assert stack.get("/metrics").status_code == 404
    assert stack.router.metrics_port != stack.router.port


def test_api_is_not_served_on_the_metrics_port(stack, world):
    r = httpx.post(
        f"{stack.router.metrics_url}/v1/chat/completions",
        headers=stack.key.auth, json={"model": w.CHAT_ALIAS, "messages": [{"role": "user", "content": "x"}]},
        timeout=5, trust_env=False,
    )  # fmt: skip

    assert r.status_code in (404, 405)
    assert not world.chat.calls("/v1/chat/completions")


def test_requests_show_up_by_route_and_outcome(stack, world):
    before = scrape(stack).text
    for _ in range(3):
        assert stack.chat(w.CHAT_ALIAS).status_code == 200
    world.chat.fail(400, times=1)
    assert stack.chat(w.CHAT_ALIAS).status_code == 400

    def counted():
        now = scrape(stack).text
        ok = total(now, w.CHAT_ROUTE, '"ok"') - total(before, w.CHAT_ROUTE, '"ok"')
        bad = total(now, w.CHAT_ROUTE, '"client_error"') - total(before, w.CHAT_ROUTE, '"client_error"')
        return ok >= 3 and bad >= 1

    wait_until(counted, timeout=5, what="request counters for the chat route to move")


def test_tokens_show_up(stack, world):
    stack.chat(w.CHAT_ALIAS, "some words to count")

    wait_until(lambda: "token" in scrape(stack).text.lower(), timeout=5, what="a token metric")


def test_build_info_is_exposed(stack, world):
    text = scrape(stack).text

    assert any("version" in labels for name, labels, _ in samples(text) if "info" in name), (
        "no build-info metric"
    )


def test_metrics_never_contain_an_api_key(stack, world):
    key = stack.new_key(name="metrics-probe-key", owner="metrics-owner")
    before = total(scrape(stack).text, w.CHAT_ROUTE)
    stack.chat(w.CHAT_ALIAS, key=key)
    stack.chat(w.CHAT_ALIAS, key=key.token + "wrong")

    text = wait_until(
        lambda: total(t := scrape(stack).text, w.CHAT_ROUTE) > before and t, timeout=5,
        what="the request to be counted",
    )  # fmt: skip
    assert key.token not in text and key.token[6:] not in text
