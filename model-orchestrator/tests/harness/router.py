"""The router under test, run as a black box: a subprocess with a config file and a log file."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, ClassVar

import httpx
import yaml

from .util import HOST, free_port

ROUTER_DIR = Path(__file__).resolve().parents[2]  # model-orchestrator/
TARGETS = ("new", "legacy")
# The package is what ships. ``legacy`` (main.py) stays selectable until it is deleted.
DEFAULT_TARGET = "new"

# Fast settings for every router the suite starts. The first block exists in both
# routers; the second came with the rework (design 7.2) and the legacy router ignores it.
# Whole seconds on purpose, so they parse whether a setting is read as int or float.
FAST_ENV = {
    "ROUTER_KEYS_REFRESH_SECONDS": "1",
    "ROUTER_UNHEALTHY_SECONDS": "2",
    "ROUTER_DISCOVERY_INTERVAL": "2",
    "ROUTER_LOG_LEVEL": "INFO",
    "ROUTER_HEALTH_INTERVAL": "1",
    "ROUTER_HEALTH_TIMEOUT": "1",
    "ROUTER_HEALTH_FAILS": "2",
    "ROUTER_CONFIG_POLL_SECONDS": "1",
    "ROUTER_SHUTDOWN_GRACE_SECONDS": "1",
    "ROUTER_METRICS_HOST": HOST,
}
_INHERITED = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


def current_target() -> str:
    value = (os.environ.get("ROUTER_TARGET") or DEFAULT_TARGET).strip().lower()
    if value not in TARGETS:
        raise ValueError(f"ROUTER_TARGET must be one of {TARGETS}, got {value!r}")
    return value


def base_command(target: str) -> list[str]:
    """How a target is started. ``ROUTER_CMD`` replaces the command of the ``new`` target."""
    if target == "legacy":
        return [sys.executable, "main.py"]
    override = os.environ.get("ROUTER_CMD")
    if override:
        return shlex.split(override)
    return [sys.executable, "-m", "model_orchestrator"]


def base_environment() -> dict[str, str]:
    """A clean environment: nothing from the developer's shell leaks into the router."""
    env = {k: os.environ[k] for k in _INHERITED if k in os.environ}
    env.update({"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


class RouterStartError(RuntimeError):
    pass


class RouterProcess:
    live: ClassVar[list[RouterProcess]] = []

    def __init__(
        self,
        *,
        name: str,
        target: str,
        workdir: Path,
        database_url: str,
        config: dict[str, Any] | str | None = None,
        env: dict[str, Any] | None = None,
        host: str = HOST,
        port: int | None = None,
    ) -> None:
        self.name = name
        self.target = target
        self.workdir = workdir
        self.database_url = database_url
        self.host = host  # the address tests connect to
        self.port = port or free_port()
        self.metrics_port = free_port()
        self.env_overrides = dict(env or {})
        self.config_path = workdir / "config.yaml"
        self.log_path = workdir / "router.log"
        self.proc: subprocess.Popen[bytes] | None = None
        if config is not None:
            self.write_config(config)

    # ---- configuration ---------------------------------------------------

    def write_config(self, config: dict[str, Any] | str) -> None:
        """Write the YAML config. Replaces the file atomically, so its mtime changes."""
        text = config if isinstance(config, str) else yaml.safe_dump(config, sort_keys=False)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, self.config_path)

    def environment(self) -> dict[str, str]:
        env = base_environment()
        env.update(FAST_ENV)
        env.update(
            {
                "ROUTER_CONFIG": str(self.config_path),
                "ROUTER_DATABASE_URL": self.database_url,
                "ROUTER_HOST": self.host,
                "ROUTER_PORT": str(self.port),
                "ROUTER_METRICS_PORT": str(self.metrics_port),
            }
        )
        for key, value in self.env_overrides.items():
            if value is None:
                env.pop(key, None)  # None = make sure the variable is unset
            else:
                env[key] = str(value)
        return env

    # ---- lifecycle -------------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def metrics_url(self) -> str:
        return f"http://{HOST}:{self.metrics_port}"

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self) -> int | None:
        return None if self.proc is None else self.proc.poll()

    def start(self, *, wait: bool = True, timeout: float = 30.0) -> RouterProcess:
        assert not self.running, f"router {self.name} is already running"
        with self.log_path.open("ab") as log:
            log.write(f"--- start {self.name} ({self.target}) on {self.base_url} ---\n".encode())
            log.flush()
            self.proc = subprocess.Popen(
                base_command(self.target),
                cwd=ROUTER_DIR,
                env=self.environment(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        if self not in RouterProcess.live:
            RouterProcess.live.append(self)
        if wait:
            self.wait_live(timeout)
        return self

    def wait_live(self, timeout: float = 30.0) -> None:
        """Wait for ``GET /livez`` to answer 200; fail loudly, with the log, if it never does."""
        deadline = time.monotonic() + timeout
        url = f"{self.base_url}/livez"
        while True:
            if not self.running:
                raise RouterStartError(
                    f"router {self.name} exited with code {self.returncode} before /livez answered\n{self.log_tail()}"
                )
            try:
                if httpx.get(url, timeout=1.0, trust_env=False).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                self.stop()
                raise RouterStartError(
                    f"router {self.name}: no /livez after {timeout:.0f}s\n{self.log_tail()}"
                )
            time.sleep(0.05)

    def wait_exit(self, timeout: float = 15.0) -> int | None:
        """Wait for the process to end by itself. Returns the exit code, or None if it is still up."""
        if self.proc is None:
            return None
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def wait_exit_or_live(self, timeout: float = 15.0) -> int | None:
        """For starts that are expected to fail: the exit code, or None once ``/livez`` answers.

        Returns as soon as either happens, so a router that wrongly starts is
        noticed in a fraction of a second instead of after ``timeout``.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running:
                return self.returncode
            try:
                if httpx.get(f"{self.base_url}/livez", timeout=0.5, trust_env=False).status_code == 200:
                    return None
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        return self.returncode

    def signal(self, signum: int) -> None:
        assert self.proc is not None
        self.proc.send_signal(signum)

    def stop(self, timeout: float = 4.0) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
        if self in RouterProcess.live:
            RouterProcess.live.remove(self)

    def restart(self, timeout: float = 30.0) -> None:
        self.stop()
        self.start(timeout=timeout)

    # ---- logs ------------------------------------------------------------

    def log_size(self) -> int:
        return self.log_path.stat().st_size if self.log_path.exists() else 0

    def log_since(self, offset: int = 0) -> str:
        if not self.log_path.exists():
            return ""
        with self.log_path.open("rb") as log:
            log.seek(offset)
            return log.read().decode("utf-8", "replace")

    def log(self) -> str:
        return self.log_since(0)

    def log_tail(self, lines: int = 60) -> str:
        tail = self.log().splitlines()[-lines:]
        return f"--- last lines of {self.log_path} ---\n" + "\n".join(tail)


def kill_all_routers() -> None:
    for router in list(RouterProcess.live):
        router.stop(timeout=1.0)
