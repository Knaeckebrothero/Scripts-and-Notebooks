"""Turn audit rows into sentences an administrator can scan."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .. import formatting

ACTIONS = {
    "key.create": "Created key",
    "key.update": "Edited key",
    "key.enable": "Enabled key",
    "key.disable": "Disabled key",
    "key.delete": "Deleted key",
    "auth.login": "Signed in",
    "auth.logout": "Signed out",
    "auth.denied": "Sign-in refused",
}
TONES = {
    "key.delete": "bad",
    "key.disable": "warn",
    "auth.denied": "bad",
    "key.create": "ok",
    "key.enable": "ok",
}
ACTOR_KINDS = {"local": "local account", "oidc": "single sign-on"}
FIELD_LABELS = {
    "name": "Name",
    "owner": "Owner",
    "owner_email": "Owner email",
    "rate_limit_rpm": "Rate limit",
    "max_concurrency": "Concurrency cap",
    "allowed_models": "Routes",
    "expires_at": "Expiry",
    "notes": "Notes",
}


def _value(field: str, value: Any) -> str:
    if field == "allowed_models":
        return ", ".join(value) if value else "all routes"
    if field == "expires_at":
        if not value:
            return "no expiry"
        try:
            return formatting.day(datetime.fromisoformat(value))
        except (TypeError, ValueError):
            return str(value)
    if field == "max_concurrency":
        return "no cap" if value is None else str(value)
    if field == "rate_limit_rpm":
        return f"{value} per minute"
    if value in (None, ""):
        return "empty"
    return str(value)


def describe(row: dict[str, Any]) -> dict[str, Any]:
    action = row["action"]
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    lines: list[str] = []

    if action == "key.update":
        for field, change in (detail.get("changes") or {}).items():
            label = FIELD_LABELS.get(field, field)
            if isinstance(change, dict):
                lines.append(f"{label}: {_value(field, change.get('from'))} → {_value(field, change.get('to'))}")
            else:
                lines.append(f"{label} changed")
    elif action == "key.create":
        lines.append(f"Owner {detail.get('owner', '–')}, {_value('rate_limit_rpm', detail.get('rate_limit_rpm'))}")
        lines.append(
            f"Routes: {_value('allowed_models', detail.get('allowed_models'))}; "
            f"{_value('expires_at', detail.get('expires_at'))}"
        )
    elif action == "key.delete":
        lines.append(
            f"Owner {detail.get('owner', '–')}; it was "
            f"{'enabled' if detail.get('was_enabled') else 'disabled'} when deleted"
        )
    elif action in ("key.enable", "key.disable"):
        if detail.get("owner"):
            lines.append(f"Owner {detail['owner']}")
    elif action in ("auth.login", "auth.logout"):
        method = detail.get("method")
        lines.append("With the local account" if method == "local" else "With single sign-on")
    elif action == "auth.denied":
        lines.append(f"The account lacks the role {detail.get('required_role', '–')}")

    target = row.get("target") or ""
    key_id = target[4:] if target.startswith("key:") and target[4:].isascii() and target[4:].isdigit() else None
    kind, _, name = row["actor"].partition(":")
    if kind not in ACTOR_KINDS or not name:  # rows written before actors carried a prefix
        kind, name = "", row["actor"]
    return {
        "id": row["id"],
        "at": row["at"],
        "actor": row["actor"],
        "actor_name": name,
        "actor_kind": ACTOR_KINDS.get(kind),
        "action": action,
        "title": ACTIONS.get(action, action),
        "tone": TONES.get(action, "idle"),
        "subject": detail.get("name"),
        "prefix": detail.get("key_prefix"),
        "key_id": key_id,
        "lines": lines,
    }
