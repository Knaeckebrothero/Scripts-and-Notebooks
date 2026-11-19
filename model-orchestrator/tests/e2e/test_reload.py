"""Config reload on SIGHUP and on a changed file (design 7.2). New router only."""

from __future__ import annotations

import signal

import pytest
import yaml

from tests.harness.stack import route
from tests.harness.util import holds_for, wait_until

CHAT = "/v1/chat/completions"
pytestmark = pytest.mark.feature("config_reload")
APPLY = 8  # seconds allowed for a reload to show


class Reloadable:
    """A router that starts with route A only; B can be added by rewriting the config."""

    def __init__(self, factory, poll_seconds: int):
        self.a = factory.fake("vllm", ["acme/a"], name="a")
        self.b = factory.fake("vllm", ["acme/b"], name="b")
        self.route_a = route("Route-A", "chat", self.a, backend_model_name="acme/a", models=["a-alias"])
        self.route_b = route("Route-B", "chat", self.b, backend_model_name="acme/b", models=["b-alias"])
        self.stack = factory.stack(
            [self.route_a], name="reload", env={"ROUTER_CONFIG_POLL_SECONDS": str(poll_seconds)}
        )
        self.router = self.stack.router

    def write(self, routes: list[dict], **top_level) -> None:
        self.router.write_config(
            {"server": {"host": "127.0.0.1", "port": 8090}, "routes": routes, **top_level}
        )

    def sighup(self) -> None:
        self.router.signal(signal.SIGHUP)

    def has(self, *ids: str) -> bool:
        return set(ids) <= set(self.stack.models())

    def lacks(self, *ids: str) -> bool:
        return not set(ids) & set(self.stack.models())


@pytest.fixture
def by_signal(factory):
    """File polling off, so only SIGHUP can trigger a reload."""
    return Reloadable(factory, poll_seconds=0)


@pytest.fixture
def by_poll(factory):
    return Reloadable(factory, poll_seconds=1)


def test_sighup_loads_a_new_route(by_signal):
    site = by_signal
    site.write([site.route_a, site.route_b])
    holds_for(lambda: site.lacks("b-alias"), seconds=2.0, interval=0.25, what="no reload without a signal")

    site.sighup()

    wait_until(
        lambda: site.has("b-alias", "acme/b"), timeout=APPLY, what="the new route (and its discovered id)"
    )
    assert site.stack.chat("b-alias").status_code == 200
    assert site.b.last(CHAT).json["model"] == "acme/b"
    assert site.stack.chat("a-alias").status_code == 200
    assert site.router.running


def test_sighup_removes_a_route(by_signal):
    site = by_signal
    site.write([site.route_a, site.route_b])
    site.sighup()
    wait_until(lambda: site.has("b-alias"), timeout=APPLY, what="route B to load")

    site.write([site.route_b])
    site.sighup()

    wait_until(lambda: site.lacks("a-alias", "acme/a"), timeout=APPLY, what="route A to disappear")
    assert site.stack.chat("a-alias").status_code == 404
    assert site.stack.chat("acme/a").status_code == 404  # its discovered id goes with the route
    assert site.stack.chat("b-alias").status_code == 200


def test_reload_applies_changed_route_settings(by_signal):
    site = by_signal
    changed = {
        **site.route_a,
        "models": ["a-renamed"],
        "request_defaults": {"temperature": 0.9},
        "owned_by": "new-owner",
    }
    site.write([changed])

    site.sighup()

    wait_until(
        lambda: site.has("a-renamed") and site.lacks("a-alias"), timeout=APPLY, what="the renamed alias"
    )
    assert site.stack.chat("a-renamed").status_code == 200
    assert site.a.last(CHAT).json["temperature"] == 0.9
    assert site.stack.models()["a-renamed"]["owned_by"] == "new-owner"


def test_reload_resyncs_the_routes_table(by_signal):
    site = by_signal
    names = lambda: {r["name"] for r in site.stack.db.execute("SELECT name FROM routes")}  # noqa: E731
    assert names() == {"Route-A"}

    site.write([site.route_b])
    site.sighup()

    wait_until(lambda: names() == {"Route-B"}, timeout=APPLY, what="the routes table to follow the config")


def test_reload_does_not_disturb_requests_in_flight(by_signal):
    from concurrent.futures import ThreadPoolExecutor

    site = by_signal
    site.a.delay(1.5, path=CHAT)
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(site.stack.chat, "a-alias")
        wait_until(lambda: site.a.calls(CHAT), timeout=3, what="the request to reach the backend")
        site.write([site.route_b])  # route A is removed while its request is running
        site.sighup()
        wait_until(lambda: site.has("b-alias"), timeout=APPLY, what="the reload")
        assert running.result(timeout=10).status_code == 200


INVALID = {
    "broken-yaml": "routes:\n  - name: [unclosed\n",
    "duplicate-alias": None,  # built in the test
    "unknown-key": None,
    "not-a-mapping": "- just\n- a\n- list\n",
    "empty-file": "",
}
# An empty file IS in this list: no routes and no upstreams is valid at startup (with a warning),
# but a reload refuses it, because a truncated file must not wipe the route table of a running router.


@pytest.mark.parametrize("problem", INVALID)
def test_invalid_config_is_rejected_and_the_running_one_stays(by_signal, problem):
    site = by_signal
    if problem == "duplicate-alias":
        text = yaml.safe_dump({"routes": [site.route_a, {**site.route_b, "models": ["a-alias"]}]})
    elif problem == "unknown-key":
        text = yaml.safe_dump({"routes": [site.route_a, {**site.route_b, "request_default": {"x": 1}}]})
    else:
        text = INVALID[problem]
    mark = site.router.log_size()
    site.router.write_config(text)

    site.sighup()

    wait_until(lambda: site.router.log_since(mark).strip(), timeout=APPLY, what="the rejection to be logged")
    holds_for(
        lambda: site.router.running and site.has("a-alias") and site.lacks("b-alias"),
        seconds=2.0, interval=0.25, what="the old config staying in force",
    )  # fmt: skip
    assert site.stack.chat("a-alias").status_code == 200

    # a corrected file loads on the next signal
    site.write([site.route_a, site.route_b])
    site.sighup()
    wait_until(lambda: site.has("b-alias"), timeout=APPLY, what="the corrected config to load")


def test_changed_file_is_picked_up_without_a_signal(by_poll):
    site = by_poll
    site.write([site.route_a, site.route_b])

    wait_until(lambda: site.has("b-alias", "acme/b"), timeout=APPLY, what="the file change to be noticed")
    assert site.stack.chat("b-alias").status_code == 200


def test_invalid_file_change_is_rejected_without_a_signal(by_poll):
    site = by_poll
    site.router.write_config("routes: {this is: not a list}\n")

    holds_for(
        lambda: site.router.running and site.has("a-alias"), seconds=3.5, interval=0.25, what="the old config"
    )
    assert site.stack.chat("a-alias").status_code == 200


def test_server_block_changes_only_apply_at_start(by_signal):
    site = by_signal
    site.router.write_config(
        {"server": {"host": "127.0.0.2", "port": 1}, "routes": [site.route_a, site.route_b]}
    )

    site.sighup()

    wait_until(lambda: site.has("b-alias"), timeout=APPLY, what="the reload")
    assert site.stack.get("/livez", key=None).status_code == 200  # still on the old address
