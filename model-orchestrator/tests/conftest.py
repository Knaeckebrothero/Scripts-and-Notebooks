"""Fixtures and the target switch for the end-to-end contract suite.

``ROUTER_TARGET=new`` (default) runs ``python -m model_orchestrator``;
``ROUTER_TARGET=legacy`` runs ``python main.py``, the 2.x router kept for
comparison. Markers decide what a test means per target:

* unmarked: the contract. Must pass on both.
* ``@pytest.mark.change("C7")``: the NEW behaviour of a row in design section 6.
  On legacy it is a strict xfail that only an assertion failure satisfies, so a
  pass, or a crash of the harness, shows up as a failure.
* ``@pytest.mark.legacy_only``: pins the "Today" column. Skipped on new.
* ``@pytest.mark.feature("name")``: section 7 behaviour. Skipped on legacy.
"""

from __future__ import annotations

import pytest

from .fakes import LoopThread
from .harness.postgres import PostgresServer
from .harness.router import RouterProcess, current_target, kill_all_routers
from .harness.stack import Stack, StackFactory
from .harness.world import World

CHANGE_IDS = {f"C{n}" for n in range(1, 23)}  # the rows of design section 6
_LOG_MARKS = pytest.StashKey[dict]()
_LOG_TAIL_LINES = 80


# ---- target switch --------------------------------------------------------


def pytest_report_header(config: pytest.Config) -> str:
    return f"router target: {current_target()} (set ROUTER_TARGET=new|legacy)"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    target = current_target()
    for item in items:
        change = item.get_closest_marker("change")
        feature = item.get_closest_marker("feature")
        legacy_only = item.get_closest_marker("legacy_only")
        if change is not None:
            cid = change.args[0] if change.args else None
            if cid not in CHANGE_IDS:
                raise pytest.UsageError(f"{item.nodeid}: change marker needs an id C1..C22, got {cid!r}")
            if legacy_only is not None:
                raise pytest.UsageError(f"{item.nodeid}: a test cannot be both 'change' and 'legacy_only'")
        if feature is not None and not (feature.args and isinstance(feature.args[0], str)):
            raise pytest.UsageError(f"{item.nodeid}: feature marker needs a name")

        if target == "legacy":
            if feature is not None:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"feature '{feature.args[0]}' does not exist in the legacy router"
                    )
                )
            elif change is not None:
                item.add_marker(
                    pytest.mark.xfail(
                        strict=True,
                        raises=AssertionError,
                        reason=f"{change.args[0]}: new behaviour, the legacy router still does what 'Today' says",
                    )
                )
        elif legacy_only is not None:
            item.add_marker(
                pytest.mark.skip(reason="pins legacy behaviour that the new router changes on purpose")
            )


# ---- router logs on failure ----------------------------------------------


@pytest.fixture(autouse=True)
def _mark_router_logs(request: pytest.FixtureRequest):
    """Remember where every running router's log is, so a failure shows only this test's lines."""
    request.node.stash[_LOG_MARKS] = {router: router.log_size() for router in RouterProcess.live}
    yield


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    report = yield
    if report.when in ("setup", "call") and report.failed:
        marks = item.stash.get(_LOG_MARKS, {})
        for router in RouterProcess.live:
            lines = router.log_since(marks.get(router, 0)).splitlines()
            if len(lines) > _LOG_TAIL_LINES:
                lines = [
                    f"... {len(lines) - _LOG_TAIL_LINES} earlier lines in {router.log_path}",
                    *lines[-_LOG_TAIL_LINES:],
                ]
            report.sections.append((f"router log: {router.name} [{router.log_path}]", "\n".join(lines)))
    return report


def pytest_sessionfinish(session: pytest.Session) -> None:
    kill_all_routers()


# ---- session infrastructure ----------------------------------------------


@pytest.fixture(scope="session")
def target() -> str:
    return current_target()


@pytest.fixture(scope="session")
def loop_thread():
    thread = LoopThread()
    yield thread
    thread.close()


@pytest.fixture(scope="session")
def postgres():
    server = PostgresServer.from_environment()
    yield server
    server.close()


def _factory(target, postgres, loop_thread, tmp_path_factory):
    factory = StackFactory(
        target=target, postgres=postgres, loop_thread=loop_thread, tmp_root=tmp_path_factory
    )
    try:
        yield factory
    finally:
        factory.close()


@pytest.fixture(scope="session")
def session_factory(target, postgres, loop_thread, tmp_path_factory):
    yield from _factory(target, postgres, loop_thread, tmp_path_factory)


@pytest.fixture(scope="module")
def module_factory(target, postgres, loop_thread, tmp_path_factory):
    """Fakes and stacks that live for one test module."""
    yield from _factory(target, postgres, loop_thread, tmp_path_factory)


@pytest.fixture
def factory(target, postgres, loop_thread, tmp_path_factory):
    """Fakes and stacks that live for one test: ``factory.fake(kind)`` and ``factory.stack(routes)``."""
    yield from _factory(target, postgres, loop_thread, tmp_path_factory)


# ---- the shared default stack --------------------------------------------


@pytest.fixture(scope="session")
def _world(session_factory) -> World:
    return World.build(session_factory)


@pytest.fixture(scope="session")
def _stack(session_factory, _world) -> Stack:
    return session_factory.stack(_world.routes(), name="default")


@pytest.fixture
def world(_world) -> World:
    """The shared fakes, reset to healthy and empty before each test that uses them."""
    _world.reset()
    yield _world
    _world.reset()


@pytest.fixture
def stack(_stack, world) -> Stack:
    """The shared default router (all seven routes of ``harness/world.py``)."""
    assert _stack.router.running, f"the shared router died\n{_stack.router.log_tail()}"
    return _stack
