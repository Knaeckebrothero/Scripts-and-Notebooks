"""Environment settings.

Every variable the previous implementation read keeps its name and default. The
new ones are listed in the design, section 7.2.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class SettingsError(ValueError):
    """An environment variable holds a value that cannot be used."""


@dataclass(frozen=True)
class Settings:
    config_path: str = "config.yaml"
    database_url: str | None = None
    host: str | None = None
    port: int = 8090
    metrics_host: str | None = None
    metrics_port: int = 9095
    log_level: str = "INFO"
    log_format: str = "text"
    chat_read_timeout_s: float = 600.0
    unhealthy_seconds: float = 30.0
    discovery_interval: float = 300.0
    keys_refresh_seconds: float = 15.0
    health_interval: float = 10.0
    health_timeout: float = 5.0
    health_fails: int = 3
    health_gating: bool = True
    max_body_mb: float = 200.0
    expose_docs: bool = False
    config_poll_seconds: float = 10.0
    # Connections to backends per network path (proxy / TLS setting), for traffic.
    upstream_max_connections: int = 1000
    # A stream is dropped when the client takes no bytes for this long. 0: never.
    client_write_timeout_s: float = 60.0
    # Concurrency cap for keys whose own max_concurrency is NULL. 0: none.
    default_max_concurrency: int = 0
    # After SIGTERM: how long requests in flight may run before they are ended.
    shutdown_grace_s: int = 8
    version: str = "unknown"
    git_sha: str = "unknown"
    migrations_dir: str = str(PROJECT_DIR / "migrations")

    @property
    def max_body_bytes(self) -> int:
        return int(self.max_body_mb * 1024 * 1024)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        log_format = _text(e, "ROUTER_LOG_FORMAT", "text").lower()
        if log_format not in ("text", "json"):
            raise SettingsError(f"ROUTER_LOG_FORMAT must be 'text' or 'json', not {log_format!r}")
        return cls(
            config_path=_text(e, "ROUTER_CONFIG", "config.yaml"),
            database_url=e.get("ROUTER_DATABASE_URL") or None,
            host=e.get("ROUTER_HOST") or None,
            port=_int(e, "ROUTER_PORT", 8090, minimum=0, maximum=65535),
            metrics_host=e.get("ROUTER_METRICS_HOST") or None,
            metrics_port=_int(e, "ROUTER_METRICS_PORT", 9095, minimum=0, maximum=65535),
            log_level=_text(e, "ROUTER_LOG_LEVEL", "INFO").upper(),
            log_format=log_format,
            chat_read_timeout_s=_float(e, "CHAT_READ_TIMEOUT_S", 600.0, above=0),
            unhealthy_seconds=_float(e, "ROUTER_UNHEALTHY_SECONDS", 30.0),
            discovery_interval=_float(e, "ROUTER_DISCOVERY_INTERVAL", 300.0, above=0),
            keys_refresh_seconds=_float(e, "ROUTER_KEYS_REFRESH_SECONDS", 15.0, above=0),
            health_interval=_float(e, "ROUTER_HEALTH_INTERVAL", 10.0, above=0),
            health_timeout=_float(e, "ROUTER_HEALTH_TIMEOUT", 5.0, above=0),
            health_fails=_int(e, "ROUTER_HEALTH_FAILS", 3, minimum=1),
            health_gating=_bool(e, "ROUTER_HEALTH_GATING", True),
            max_body_mb=_float(e, "ROUTER_MAX_BODY_MB", 200.0, above=0),
            expose_docs=_bool(e, "ROUTER_EXPOSE_DOCS", False),
            config_poll_seconds=_float(e, "ROUTER_CONFIG_POLL_SECONDS", 10.0),
            upstream_max_connections=_int(e, "ROUTER_UPSTREAM_MAX_CONNECTIONS", 1000, minimum=1),
            client_write_timeout_s=_float(e, "ROUTER_CLIENT_WRITE_TIMEOUT", 60.0),
            default_max_concurrency=_int(e, "ROUTER_DEFAULT_MAX_CONCURRENCY", 0, minimum=0),
            shutdown_grace_s=_int(e, "ROUTER_SHUTDOWN_GRACE_SECONDS", 8, minimum=0),
            version=e.get("ROUTER_VERSION") or _fallback_version(),
            git_sha=e.get("ROUTER_GIT_SHA") or "unknown",
            migrations_dir=e.get("ROUTER_MIGRATIONS_DIR") or str(PROJECT_DIR / "migrations"),
        )


def _text(env: Mapping[str, str], name: str, default: str) -> str:
    return env.get(name) or default


def _int(
    env: Mapping[str, str], name: str, default: int, *, minimum: int | None = None, maximum: int | None = None
) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise SettingsError(f"{name} must be an integer, not {raw!r}") from None
    if minimum is not None and value < minimum:
        raise SettingsError(f"{name} must be at least {minimum}, not {value}")
    if maximum is not None and value > maximum:
        raise SettingsError(f"{name} must be at most {maximum}, not {value}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, *, above: float | None = None) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        raise SettingsError(f"{name} must be a number, not {raw!r}") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise SettingsError(f"{name} must be a finite number, not {raw!r}")
    if above is not None and value <= above:
        raise SettingsError(f"{name} must be greater than {above:g}, not {value:g}")
    if value < 0:
        raise SettingsError(f"{name} must not be negative, not {value:g}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise SettingsError(f"{name} must be one of 1/0, true/false, yes/no, on/off, not {raw!r}")


def _fallback_version() -> str:
    """Version when ``ROUTER_VERSION`` is not set (a source checkout, not an image)."""
    try:
        return metadata.version("model-orchestrator")
    except metadata.PackageNotFoundError:
        pass
    try:
        with open(PROJECT_DIR / "pyproject.toml", "rb") as handle:
            version = tomllib.load(handle).get("project", {}).get("version")
    except (OSError, tomllib.TOMLDecodeError):
        return "unknown"
    return version if isinstance(version, str) and version else "unknown"
