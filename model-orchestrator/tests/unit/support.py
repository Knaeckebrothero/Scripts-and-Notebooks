"""Small builders shared by the unit tests."""

from __future__ import annotations

from typing import Any

from model_orchestrator.registry import ROUTE, UPSTREAM, ClientKey, Target


def route_target(name: str = "Chat", **overrides: Any) -> Target:
    base: dict[str, Any] = {
        "kind": ROUTE,
        "name": name,
        "backends": ("http://backend-a.invalid:8000",),
        "client_key": ClientKey(),
        "api_key": None,
        "health_path": "/health",
        "discovery_interval": 300.0,
        "route_type": "chat",
        "endpoint": "/v1/chat/completions",
    }
    base.update(overrides)
    return Target(**base)


def upstream_target(name: str = "site-a", **overrides: Any) -> Target:
    base: dict[str, Any] = {
        "kind": UPSTREAM,
        "name": name,
        "backends": ("http://site-a-router.invalid:8091",),
        "client_key": ClientKey(),
        "api_key": "upstream-key",
        "health_path": "/livez",
        "discovery_interval": 60.0,
    }
    base.update(overrides)
    return Target(**base)


def listing(*ids: str, **extra: Any) -> list[dict[str, Any]]:
    return [{"id": model_id, "object": "model", "owned_by": "backend", **extra} for model_id in ids]
