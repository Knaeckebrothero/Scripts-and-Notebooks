"""API keys: token format, validation, CRUD and the audit trail of each change.

The token format and hashing must stay exactly as the router expects:
``"sk-mo-" + 40 hex characters``, stored as the SHA-256 hex digest of the
whole token plus its first 12 characters for display. The plaintext exists
only in the response that creates the key.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from psycopg.types.json import Jsonb

from .db import Database

KEY_PREFIX_LEN = 12
TOKEN_PATTERN = re.compile(r"^sk-mo-[0-9a-f]{40}$")
EXPIRING_SOON = timedelta(days=14)

MAX_NAME = 100
MAX_NOTES = 2000
MAX_EXPIRY_AHEAD = timedelta(days=3653)  # ten years
MAX_RPM = 1_000_000
MAX_CONCURRENCY = 10_000
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Actor:
    """Who is making a change, as it goes into the audit log.

    ``name`` is ``local:<user>`` or ``oidc:<username>``; ``subject`` is the
    identity provider's stable id for OIDC users, which survives a rename.
    """

    name: str
    subject: str | None = None

    def stamp(self, detail: dict[str, Any]) -> dict[str, Any]:
        return {**detail, "subject": self.subject} if self.subject else detail


def _has_control_characters(text: str, *, multiline: bool = False) -> bool:
    allowed = "\n\r\t" if multiline else ""
    return any((ord(ch) < 0x20 or ord(ch) == 0x7F) and ch not in allowed for ch in text)


def generate_token() -> str:
    return "sk-mo-" + secrets.token_hex(20)


def hash_token(token: str) -> str:
    """SHA-256 hex of the full token. Must match ``hash_key`` in the router."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------- status
@dataclass(frozen=True)
class KeyStatus:
    code: str    # active | expiring | expired | disabled
    label: str
    tone: str    # ok | warn | bad | idle


def key_status(row: dict[str, Any], now: datetime) -> KeyStatus:
    expires = row.get("expires_at")
    if not row.get("enabled"):
        return KeyStatus("disabled", "Disabled", "idle")
    if expires is not None and expires <= now:
        return KeyStatus("expired", "Expired", "bad")
    if expires is not None and expires - now <= EXPIRING_SOON:
        return KeyStatus("expiring", "Expires soon", "warn")
    return KeyStatus("active", "Active", "ok")


# ----------------------------------------------------------------- form input
@dataclass
class KeyInput:
    name: str
    owner: str
    owner_email: str | None
    rate_limit_rpm: int
    max_concurrency: int | None
    allowed_models: list[str] | None
    expires_at: datetime | None
    notes: str | None


def _end_of_day(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=timezone.utc)


def parse_key_form(
    form: Any,
    *,
    route_names: list[str],
    keep_routes: list[str] | None = None,
    keep_expiry: datetime | None = None,
    today: date | None = None,
) -> tuple[KeyInput | None, dict[str, str], dict[str, Any]]:
    """Validate the create/edit form.

    Returns ``(input, errors, values)``. ``values`` echoes what was typed so
    the form can be shown again. ``keep_routes`` are route names a key
    already carries; they stay selectable even when the router no longer has
    such a route, so editing an unrelated field never silently widens or
    narrows access. ``keep_expiry`` is the expiry a key already has: leaving
    it as it is stays possible even when it is further ahead than a new one
    may be.
    """
    today = today or datetime.now(timezone.utc).date()
    errors: dict[str, str] = {}

    def text(field: str) -> str:
        value = form.get(field)
        return value.strip() if isinstance(value, str) else ""

    name, owner, email = text("name"), text("owner"), text("owner_email")
    rpm_raw, conc_raw = text("rate_limit_rpm"), text("max_concurrency")
    expiry_raw, notes = text("expires_on"), text("notes")
    access = text("access") or "all"
    chosen = [v for v in form.getlist("allowed_models") if isinstance(v, str)]

    values: dict[str, Any] = {
        "name": name, "owner": owner, "owner_email": email, "rate_limit_rpm": rpm_raw,
        "max_concurrency": conc_raw, "expires_on": expiry_raw, "notes": notes,
        "access": access, "allowed_models": chosen,
    }

    if not name:
        errors["name"] = "Give the key a name."
    elif _has_control_characters(name):
        errors["name"] = "Remove the control characters from the name."
    elif len(name) > MAX_NAME:
        errors["name"] = f"Keep the name under {MAX_NAME} characters."
    if not owner:
        errors["owner"] = "Say who owns the key."
    elif _has_control_characters(owner):
        errors["owner"] = "Remove the control characters from the owner."
    elif len(owner) > MAX_NAME:
        errors["owner"] = f"Keep the owner under {MAX_NAME} characters."
    if email and (len(email) > 254 or not _EMAIL.match(email) or _has_control_characters(email)):
        errors["owner_email"] = "Enter an address like name@example.org, or leave it empty."

    rpm = 0
    try:
        rpm = int(rpm_raw)
        if not 1 <= rpm <= MAX_RPM:
            raise ValueError
    except ValueError:
        errors["rate_limit_rpm"] = f"Enter a whole number from 1 to {MAX_RPM:,}."

    concurrency: int | None = None
    if conc_raw:
        try:
            concurrency = int(conc_raw)
            if not 1 <= concurrency <= MAX_CONCURRENCY:
                raise ValueError
        except ValueError:
            errors["max_concurrency"] = f"Enter a whole number from 1 to {MAX_CONCURRENCY:,}, or leave it empty for no cap."

    expires_at: datetime | None = None
    if expiry_raw:
        try:
            expiry_day = date.fromisoformat(expiry_raw)
        except ValueError:
            errors["expires_on"] = "Enter a date as YYYY-MM-DD, or leave it empty."
        else:
            unchanged = keep_expiry is not None and keep_expiry.astimezone(timezone.utc).date() == expiry_day
            if unchanged:
                expires_at = keep_expiry
            elif expiry_day - today > MAX_EXPIRY_AHEAD:
                errors["expires_on"] = "Pick a date at most 10 years ahead, or leave it empty for no expiry."
            else:
                expires_at = _end_of_day(expiry_day)

    allowed: list[str] | None = None
    if access == "some":
        valid = set(route_names) | set(keep_routes or [])
        unknown = [r for r in chosen if r not in valid]
        if unknown:
            errors["allowed_models"] = "Unknown route: " + ", ".join(sorted(unknown))
        elif not chosen:
            errors["allowed_models"] = "Pick at least one route, or allow all routes."
        else:
            allowed = sorted(set(chosen))
    elif access != "all":
        errors["allowed_models"] = "Choose all routes or specific routes."

    if len(notes) > MAX_NOTES:
        errors["notes"] = f"Keep notes under {MAX_NOTES:,} characters."
    elif _has_control_characters(notes, multiline=True):
        errors["notes"] = "Remove the control characters from the notes."

    if errors:
        return None, errors, values
    return (
        KeyInput(
            name=name, owner=owner, owner_email=email or None, rate_limit_rpm=rpm,
            max_concurrency=concurrency, allowed_models=allowed, expires_at=expires_at,
            notes=notes or None,
        ),
        errors,
        values,
    )


def form_values_from_row(row: dict[str, Any]) -> dict[str, Any]:
    allowed = list(row.get("allowed_models") or [])
    expires = row.get("expires_at")
    return {
        "name": row["name"],
        "owner": row["owner"],
        "owner_email": row.get("owner_email") or "",
        "rate_limit_rpm": str(row["rate_limit_rpm"]),
        "max_concurrency": "" if row.get("max_concurrency") is None else str(row["max_concurrency"]),
        "expires_on": expires.astimezone(timezone.utc).date().isoformat() if expires else "",
        "notes": row.get("notes") or "",
        "access": "some" if allowed else "all",
        "allowed_models": allowed,
    }


# ------------------------------------------------------------------- audit
def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    return value


async def write_audit(
    conn: Any, actor: Actor, action: str, target: str | None, detail: dict[str, Any] | None
) -> None:
    detail = actor.stamp(detail or {}) or None
    await conn.execute(
        "INSERT INTO admin_audit (actor, action, target, detail) VALUES (%s, %s, %s, %s)",
        (actor.name, action, target, Jsonb(detail) if detail is not None else None),
    )


def _target(key_id: int) -> str:
    return f"key:{key_id}"


# -------------------------------------------------------------------- CRUD
KEY_COLUMNS = """
    id, key_prefix, name, owner, owner_email, rate_limit_rpm, max_concurrency,
    allowed_models, enabled, expires_at, created_at, last_used_at, created_by, notes
"""


async def list_routes(db: Database) -> list[dict[str, Any]]:
    return await db.fetch_all("SELECT name, type, updated_at FROM routes ORDER BY lower(name)")


async def get_key(db: Database, key_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id = %s", (key_id,))


async def create_key(db: Database, data: KeyInput, actor: Actor) -> tuple[int, str]:
    """Insert a key and return ``(id, plaintext token)``. The token is not kept."""
    token = generate_token()
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            INSERT INTO api_keys
                (key_hash, key_prefix, name, owner, owner_email, rate_limit_rpm,
                 max_concurrency, allowed_models, enabled, expires_at, notes, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
            RETURNING id
            """,
            (
                hash_token(token), token[:KEY_PREFIX_LEN], data.name, data.owner, data.owner_email,
                data.rate_limit_rpm, data.max_concurrency, data.allowed_models, data.expires_at,
                data.notes, actor.name,
            ),
        )
        key_id = (await cur.fetchone())["id"]
        await write_audit(
            conn, actor, "key.create", _target(key_id),
            {
                "name": data.name,
                "owner": data.owner,
                "owner_email": data.owner_email,
                "key_prefix": token[:KEY_PREFIX_LEN],
                "rate_limit_rpm": data.rate_limit_rpm,
                "max_concurrency": data.max_concurrency,
                "allowed_models": data.allowed_models,
                "expires_at": _plain(data.expires_at),
                "has_notes": bool(data.notes),
            },
        )
    return key_id, token


_EDITABLE = (
    "name", "owner", "owner_email", "rate_limit_rpm", "max_concurrency",
    "allowed_models", "expires_at", "notes",
)


async def update_key(db: Database, key_id: int, data: KeyInput, actor: Actor) -> dict[str, Any] | None:
    """Apply an edit. Returns the changed fields (empty when nothing changed),
    or ``None`` when the key no longer exists."""
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id = %s FOR UPDATE", (key_id,)
        )
        before = await cur.fetchone()
        if before is None:
            return None
        changes: dict[str, Any] = {}
        for column in _EDITABLE:
            old, new = before[column], getattr(data, column)
            if column == "allowed_models":
                old = sorted(old) if old else None
            if old != new:
                if column == "notes":
                    changes[column] = "changed"  # free text stays out of the audit log
                else:
                    changes[column] = {"from": _plain(old), "to": _plain(new)}
        if not changes:
            return {}
        await conn.execute(
            """
            UPDATE api_keys
            SET name = %s, owner = %s, owner_email = %s, rate_limit_rpm = %s,
                max_concurrency = %s, allowed_models = %s, expires_at = %s, notes = %s
            WHERE id = %s
            """,
            (
                data.name, data.owner, data.owner_email, data.rate_limit_rpm, data.max_concurrency,
                data.allowed_models, data.expires_at, data.notes, key_id,
            ),
        )
        await write_audit(
            conn, actor, "key.update", _target(key_id),
            {"name": data.name, "key_prefix": before["key_prefix"], "changes": changes},
        )
        return changes


async def set_enabled(db: Database, key_id: int, enabled: bool, actor: Actor) -> dict[str, Any] | None:
    """Enable or disable a key. Idempotent: no audit row when nothing changes."""
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id = %s FOR UPDATE", (key_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        if row["enabled"] != enabled:
            await conn.execute("UPDATE api_keys SET enabled = %s WHERE id = %s", (enabled, key_id))
            await write_audit(
                conn, actor, "key.enable" if enabled else "key.disable", _target(key_id),
                {"name": row["name"], "owner": row["owner"], "key_prefix": row["key_prefix"]},
            )
            row["enabled"] = enabled
        return row


async def delete_key(db: Database, key_id: int, actor: Actor) -> dict[str, Any] | None:
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            f"DELETE FROM api_keys WHERE id = %s RETURNING {KEY_COLUMNS}", (key_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        await write_audit(
            conn, actor, "key.delete", _target(key_id),
            {
                "name": row["name"],
                "owner": row["owner"],
                "owner_email": row["owner_email"],
                "key_prefix": row["key_prefix"],
                "was_enabled": row["enabled"],
                "created_at": _plain(row["created_at"]),
                "last_used_at": _plain(row["last_used_at"]),
            },
        )
        return row


# ----------------------------------------------------------------- listing
SORTS = {
    "created": lambda r: r["created_at"],
    "name": lambda r: r["name"].lower(),
    "owner": lambda r: r["owner"].lower(),
    "status": lambda r: ("active", "expiring", "expired", "disabled").index(r["status"].code),
    "limit": lambda r: r["rate_limit_rpm"],
    "expires": lambda r: r["expires_at"] or datetime.max.replace(tzinfo=timezone.utc),
    "last_used": lambda r: r["last_used_at"] or datetime.min.replace(tzinfo=timezone.utc),
    "requests": lambda r: r["requests_30d"],
    "tokens": lambda r: r["tokens_30d"],
}
DEFAULT_DIRECTION = {
    "created": "desc", "name": "asc", "owner": "asc", "status": "asc", "limit": "desc",
    "expires": "asc", "last_used": "desc", "requests": "desc", "tokens": "desc",
}
FILTERS = ("all", "active", "expiring", "expired", "disabled", "unused")


async def list_keys(db: Database, now: datetime) -> list[dict[str, Any]]:
    """Every key with its 30-day requests (usage_daily) and tokens (usage_hourly)."""
    today = now.astimezone(timezone.utc).date()
    since_day = today - timedelta(days=29)
    since_hour = datetime.combine(since_day, time.min, tzinfo=timezone.utc)
    rows = await db.fetch_all(
        f"""
        SELECT {KEY_COLUMNS},
               COALESCE(d.requests, 0) AS requests_30d,
               COALESCE(d.errors, 0)   AS errors_30d,
               COALESCE(h.tokens, 0)   AS tokens_30d
        FROM api_keys k
        LEFT JOIN (
            SELECT api_key_id, SUM(request_count) AS requests, SUM(error_count) AS errors
            FROM usage_daily WHERE day >= %(since_day)s GROUP BY api_key_id
        ) d ON d.api_key_id = k.id
        LEFT JOIN (
            SELECT api_key_id, SUM(prompt_tokens + completion_tokens) AS tokens
            FROM usage_hourly WHERE hour >= %(since_hour)s GROUP BY api_key_id
        ) h ON h.api_key_id = k.id
        """,
        {"since_day": since_day, "since_hour": since_hour},
    )
    for row in rows:
        decorate(row, now)
    return rows


def decorate(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    row["status"] = key_status(row, now)
    row["never_used"] = row.get("last_used_at") is None and not row.get("requests_30d")
    row["requests_30d"] = int(row.get("requests_30d") or 0)
    row["errors_30d"] = int(row.get("errors_30d") or 0)
    row["tokens_30d"] = int(row.get("tokens_30d") or 0)
    return row


def filter_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(FILTERS, 0)
    counts["all"] = len(rows)
    for row in rows:
        counts[row["status"].code] += 1
        if row["never_used"]:
            counts["unused"] += 1
    return counts


def select_keys(
    rows: list[dict[str, Any]], *, query: str = "", status: str = "all", sort: str = "created", direction: str = ""
) -> list[dict[str, Any]]:
    needle = query.strip().lower()
    if needle:
        def matches(row: dict[str, Any]) -> bool:
            haystack = " ".join(
                str(row.get(f) or "") for f in ("name", "owner", "owner_email", "key_prefix", "notes", "created_by")
            ).lower()
            return all(part in haystack for part in needle.split())
        rows = [r for r in rows if matches(r)]
    if status == "unused":
        rows = [r for r in rows if r["never_used"]]
    elif status in FILTERS and status != "all":
        rows = [r for r in rows if r["status"].code == status]
    sort = sort if sort in SORTS else "created"
    direction = direction if direction in ("asc", "desc") else DEFAULT_DIRECTION[sort]
    rows = sorted(rows, key=lambda r: r["name"].lower())
    return sorted(rows, key=SORTS[sort], reverse=direction == "desc")
