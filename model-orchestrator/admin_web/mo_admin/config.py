"""Settings, read once from the environment.

Every variable is prefixed ``ADMIN_``. ``load_settings`` raises
``ConfigError`` with a message that names the variable, so a bad deployment
fails at start-up instead of at the first login.
"""
from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger("mo_admin.config")

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
MIN_LOCAL_PASSWORD = 12
MAX_SESSION_HOURS = 72  # a copied cookie is good for at most this long


class ConfigError(RuntimeError):
    """The environment does not describe a runnable configuration."""


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} must be on or off, not {raw!r}")


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, not {raw!r}") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


def _url(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name, "").strip().rstrip("/")
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(f"{name} must be an http(s) URL, not {raw!r}")
    return raw


@dataclass(frozen=True)
class Settings:
    database_url: str
    database_timeout: float = 5.0
    router_url: str | None = None
    site_name: str = "Model Orchestrator"
    public_url: str | None = None
    trust_proxy_headers: bool = True

    session_secret: str = ""
    session_hours: int = 8
    session_idle_minutes: int = 60

    local_login: bool = True
    local_user: str = "admin"
    local_password: str = ""
    login_max_attempts: int = 5
    login_lockout_seconds: int = 60

    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_required_role: str | None = None
    oidc_role_claim: str = "realm_access.roles"
    oidc_scopes: str = "openid profile email"
    oidc_username_claim: str = "preferred_username"
    oidc_label: str = "single sign-on"

    key_refresh_seconds: int = 15
    host: str = "127.0.0.1"
    port: int = 8600

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer)

    @property
    def local_enabled(self) -> bool:
        return self.local_login and bool(self.local_password)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    database_url = (env.get("ADMIN_DATABASE_URL") or env.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise ConfigError("ADMIN_DATABASE_URL is required (postgresql://user:password@host:port/db)")

    secret = env.get("ADMIN_SESSION_SECRET", "").strip()
    if secret and len(secret) < 32:
        raise ConfigError("ADMIN_SESSION_SECRET must be at least 32 characters; try `openssl rand -hex 32`")
    if not secret:
        secret = secrets.token_hex(32)
        logger.warning(
            "ADMIN_SESSION_SECRET is not set: using a random secret, so every restart signs everyone out"
        )

    issuer = _url(env, "ADMIN_OIDC_ISSUER")
    client_id = env.get("ADMIN_OIDC_CLIENT_ID", "").strip() or None
    client_secret = env.get("ADMIN_OIDC_CLIENT_SECRET", "").strip() or None
    required_role = env.get("ADMIN_OIDC_REQUIRED_ROLE", "").strip() or None
    if issuer or client_id or client_secret or required_role:
        if not issuer:
            raise ConfigError("ADMIN_OIDC_ISSUER is required when any ADMIN_OIDC_* variable is set")
        if not client_id:
            raise ConfigError("ADMIN_OIDC_CLIENT_ID is required when ADMIN_OIDC_ISSUER is set")
        if not required_role:
            raise ConfigError(
                "ADMIN_OIDC_REQUIRED_ROLE is required when ADMIN_OIDC_ISSUER is set: "
                "without it every account of the provider would be an administrator"
            )
        parts = urlsplit(issuer)
        if parts.scheme != "https" and parts.hostname not in _LOOPBACK:
            raise ConfigError("ADMIN_OIDC_ISSUER must use https (plain http is accepted for localhost only)")

    role_claim = env.get("ADMIN_OIDC_ROLE_CLAIM", "").strip() or "realm_access.roles"

    local_password = env.get("ADMIN_LOCAL_PASSWORD", "")
    local_login = _flag(env, "ADMIN_LOCAL_LOGIN", True)
    if local_login and local_password and len(local_password) < MIN_LOCAL_PASSWORD:
        # Guessing is only slowed down, never locked out for good, so the
        # password itself has to carry the weight.
        raise ConfigError(
            f"ADMIN_LOCAL_PASSWORD must be at least {MIN_LOCAL_PASSWORD} characters "
            "(try `openssl rand -base64 24`), or set ADMIN_LOCAL_LOGIN=off"
        )

    return Settings(
        database_url=database_url,
        database_timeout=float(_int(env, "ADMIN_DATABASE_TIMEOUT_SECONDS", 5, 1, 60)),
        router_url=_url(env, "ADMIN_ROUTER_URL"),
        site_name=env.get("ADMIN_SITE_NAME", "").strip() or "Model Orchestrator",
        public_url=_url(env, "ADMIN_PUBLIC_URL"),
        trust_proxy_headers=_flag(env, "ADMIN_TRUST_PROXY_HEADERS", True),
        session_secret=secret,
        session_hours=_int(env, "ADMIN_SESSION_HOURS", 8, 1, MAX_SESSION_HOURS),
        session_idle_minutes=_int(env, "ADMIN_SESSION_IDLE_MINUTES", 60, 5, 24 * 60),
        local_login=local_login,
        local_user=env.get("ADMIN_LOCAL_USER", "").strip() or "admin",
        local_password=local_password,
        login_max_attempts=_int(env, "ADMIN_LOGIN_MAX_ATTEMPTS", 5, 1, 100),
        login_lockout_seconds=_int(env, "ADMIN_LOGIN_LOCKOUT_SECONDS", 60, 1, 3600),
        oidc_issuer=issuer,
        oidc_client_id=client_id,
        oidc_client_secret=client_secret,
        oidc_required_role=required_role,
        oidc_role_claim=role_claim,
        oidc_scopes=env.get("ADMIN_OIDC_SCOPES", "").strip() or "openid profile email",
        oidc_username_claim=env.get("ADMIN_OIDC_USERNAME_CLAIM", "").strip() or "preferred_username",
        oidc_label=env.get("ADMIN_OIDC_LABEL", "").strip() or "single sign-on",
        key_refresh_seconds=_int(env, "ADMIN_KEY_REFRESH_SECONDS", 15, 1, 3600),
        host=env.get("ADMIN_HOST", "").strip() or "127.0.0.1",
        port=_int(env, "ADMIN_PORT", 8600, 1, 65535),
    )
