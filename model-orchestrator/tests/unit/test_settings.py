"""Environment settings: existing names and defaults kept, new ones as in design 7.2."""

from __future__ import annotations

import pytest

from model_orchestrator.settings import Settings, SettingsError


def test_defaults() -> None:
    s = Settings.from_env({})
    assert s.config_path == "config.yaml"
    assert s.database_url is None
    assert s.host is None and s.port == 8090
    assert s.metrics_host is None and s.metrics_port == 9095
    assert s.log_level == "INFO" and s.log_format == "text"
    assert s.chat_read_timeout_s == 600.0
    assert s.unhealthy_seconds == 30.0
    assert s.discovery_interval == 300.0
    assert s.keys_refresh_seconds == 15.0
    assert s.health_interval == 10.0 and s.health_timeout == 5.0 and s.health_fails == 3
    assert s.health_gating is True
    assert s.max_body_mb == 200.0 and s.max_body_bytes == 200 * 1024 * 1024
    assert s.expose_docs is False
    assert s.config_poll_seconds == 10.0
    assert s.upstream_max_connections == 1000
    assert s.client_write_timeout_s == 60.0
    assert s.default_max_concurrency == 0, "off: existing keys behave as before"
    assert s.shutdown_grace_s == 8
    assert s.git_sha == "unknown"
    assert s.migrations_dir.endswith("migrations")


def test_every_variable_is_read() -> None:
    s = Settings.from_env(
        {
            "ROUTER_CONFIG": "/etc/router/config.yaml",
            "ROUTER_DATABASE_URL": "postgresql://router@db.invalid/router",
            "ROUTER_HOST": "127.0.0.1",
            "ROUTER_PORT": "8091",
            "ROUTER_METRICS_HOST": "0.0.0.0",
            "ROUTER_METRICS_PORT": "9100",
            "ROUTER_LOG_LEVEL": "debug",
            "ROUTER_LOG_FORMAT": "JSON",
            "CHAT_READ_TIMEOUT_S": "900",
            "ROUTER_UNHEALTHY_SECONDS": "0",
            "ROUTER_DISCOVERY_INTERVAL": "60",
            "ROUTER_KEYS_REFRESH_SECONDS": "5",
            "ROUTER_HEALTH_INTERVAL": "2.5",
            "ROUTER_HEALTH_TIMEOUT": "1",
            "ROUTER_HEALTH_FAILS": "5",
            "ROUTER_HEALTH_GATING": "off",
            "ROUTER_MAX_BODY_MB": "10",
            "ROUTER_EXPOSE_DOCS": "1",
            "ROUTER_CONFIG_POLL_SECONDS": "0",
            "ROUTER_UPSTREAM_MAX_CONNECTIONS": "64",
            "ROUTER_CLIENT_WRITE_TIMEOUT": "0",
            "ROUTER_DEFAULT_MAX_CONCURRENCY": "8",
            "ROUTER_SHUTDOWN_GRACE_SECONDS": "5",
            "ROUTER_VERSION": "3.0.0",
            "ROUTER_GIT_SHA": "abc1234",
            "ROUTER_MIGRATIONS_DIR": "/app/migrations",
        }
    )
    assert (s.config_path, s.database_url) == (
        "/etc/router/config.yaml",
        "postgresql://router@db.invalid/router",
    )
    assert (s.host, s.port, s.metrics_host, s.metrics_port) == ("127.0.0.1", 8091, "0.0.0.0", 9100)
    assert (s.log_level, s.log_format) == ("DEBUG", "json")
    assert (s.chat_read_timeout_s, s.unhealthy_seconds, s.discovery_interval, s.keys_refresh_seconds) == (
        900.0,
        0.0,
        60.0,
        5.0,
    )
    assert (s.health_interval, s.health_timeout, s.health_fails, s.health_gating) == (2.5, 1.0, 5, False)
    assert (s.max_body_mb, s.expose_docs, s.config_poll_seconds) == (10.0, True, 0.0)
    assert (s.version, s.git_sha, s.migrations_dir) == ("3.0.0", "abc1234", "/app/migrations")
    assert (s.upstream_max_connections, s.client_write_timeout_s) == (64, 0.0)
    assert (s.default_max_concurrency, s.shutdown_grace_s) == (8, 5)


def test_empty_values_mean_unset() -> None:
    s = Settings.from_env(
        {
            "ROUTER_HOST": "",
            "ROUTER_PORT": "",
            "ROUTER_DATABASE_URL": "",
            "ROUTER_HEALTH_GATING": " ",
            "ROUTER_VERSION": "",
        }
    )
    assert s.host is None and s.port == 8090 and s.database_url is None and s.health_gating is True
    assert s.version != ""


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_true_values(value: str) -> None:
    assert Settings.from_env({"ROUTER_EXPOSE_DOCS": value}).expose_docs is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_false_values(value: str) -> None:
    assert Settings.from_env({"ROUTER_HEALTH_GATING": value}).health_gating is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ROUTER_PORT", "http"),
        ("ROUTER_PORT", "70000"),
        ("ROUTER_PORT", "-1"),
        ("ROUTER_METRICS_PORT", "9095.5"),
        ("ROUTER_HEALTH_FAILS", "0"),
        ("ROUTER_HEALTH_INTERVAL", "0"),
        ("ROUTER_HEALTH_TIMEOUT", "-2"),
        ("CHAT_READ_TIMEOUT_S", "ten minutes"),
        ("CHAT_READ_TIMEOUT_S", "nan"),
        ("ROUTER_MAX_BODY_MB", "inf"),
        ("ROUTER_UNHEALTHY_SECONDS", "-1"),
        ("ROUTER_CONFIG_POLL_SECONDS", "-5"),
        ("ROUTER_HEALTH_GATING", "maybe"),
        ("ROUTER_UPSTREAM_MAX_CONNECTIONS", "0"),
        ("ROUTER_CLIENT_WRITE_TIMEOUT", "-1"),
        ("ROUTER_DEFAULT_MAX_CONCURRENCY", "-1"),
        ("ROUTER_SHUTDOWN_GRACE_SECONDS", "soon"),
        ("ROUTER_SHUTDOWN_GRACE_SECONDS", "-1"),
        ("ROUTER_LOG_FORMAT", "xml"),
    ],
)
def test_bad_values_name_the_variable(name: str, value: str) -> None:
    with pytest.raises(SettingsError) as caught:
        Settings.from_env({name: value})
    assert name in str(caught.value)


def test_version_falls_back_to_the_project_version() -> None:
    version = Settings.from_env({}).version
    assert version and version != "unknown", "a source checkout knows its version from pyproject.toml"
