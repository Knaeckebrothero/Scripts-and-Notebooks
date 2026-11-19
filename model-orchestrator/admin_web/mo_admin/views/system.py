"""Liveness, the models page, the audit log and the small live partials."""
from __future__ import annotations

import re
from datetime import timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import PlainTextResponse, Response

from .. import usage
from ..charts import sparkline
from ..db import Database
from ..deps import BadRequest, get_ctx, query_string, render, require_db, require_user, utcnow
from ..keys import list_keys, list_routes
from .audit_text import ACTIONS, describe
from .overview import day_buckets

public = APIRouter()
router = APIRouter(dependencies=[Depends(require_user)])

AUDIT_PAGE = 50
_ROW_ID = re.compile(r"[0-9]{1,18}")  # ASCII digits, and it fits a bigint
AUDIT_KINDS = {"all": "Everything", "key": "Key changes", "auth": "Sign-ins"}


@public.get("/livez", include_in_schema=False)
async def livez() -> Response:
    """Cheap liveness probe: no database, no router, no session."""
    return PlainTextResponse("ok")


@router.get("/partials/router-lamp")
async def router_lamp(request: Request) -> Response:
    status = await get_ctx(request).router.snapshot()
    return render(request, "partials/router_lamp.html", {"router": status, "lamp_loaded": True})


@router.get("/models")
async def models_page(request: Request, db: Database = Depends(require_db)) -> Response:
    ctx = get_ctx(request)
    now = utcnow()
    today = now.astimezone(timezone.utc).date()
    status = await ctx.router.snapshot()
    start = today - timedelta(days=6)

    routes = await list_routes(db)
    by_route = await usage.by_day(db, start, today, "route")
    per_route = usage.by_group(by_route)
    slots = await ctx.ranking.slots("route", today)

    # Model ids seen in traffic, per route: a route that is down is not listed
    # by a newer router, and an older router does not say which route serves an id.
    seen_rows = await db.fetch_all(
        """
        SELECT route, model, SUM(requests) AS n
        FROM usage_hourly WHERE hour >= %s GROUP BY 1, 2 ORDER BY n DESC
        """,
        (usage.day_start(today - timedelta(days=29)),),
    )
    seen: dict[str, list[str]] = {}
    for row in seen_rows:
        seen.setdefault(row["route"], []).append(row["model"])

    keys = await list_keys(db, now)
    restricted: dict[str, int] = {}
    for key in keys:
        for name in key.get("allowed_models") or []:
            restricted[name] = restricted.get(name, 0) + 1
    open_keys = sum(1 for key in keys if not key.get("allowed_models"))

    live = status.models_by_route()
    types = {r["name"]: r["type"] for r in routes}
    names = set(types) | set(status.routes) | {str(g) for g in per_route} | {r for r in live if r}
    days = day_buckets(start, 7)
    table: list[dict[str, Any]] = []
    for name in sorted(names, key=str.lower):
        listed = [m.id for m in live.get(name, [])]
        route_type = types.get(name) or next((m.type for m in live.get(name, []) if m.type), None)
        table.append(
            {
                "name": name,
                "type": route_type,
                "configured": name in types or name in status.routes,
                "state": status.route_state(name),
                "listed": listed,
                "seen": [m for m in seen.get(name, []) if m not in listed][:6],
                "cell": per_route.get(name, usage.Cell()),
                "slot": slots.get(name, 0),
                "spark": sparkline(
                    [by_route[(d, name)].requests if (d, name) in by_route else 0 for d in days], "Requests per day"
                ),
                "restricted_keys": restricted.get(name, 0),
            }
        )
    table.sort(key=lambda r: ({"down": 0, "unknown": 1, "up": 2}[r["state"]] if status.reachable else 1, r["name"].lower()))

    return render(
        request,
        "models.html",
        {
            "active": "models",
            "router": status,
            "routes": table,
            "unassigned": [m for m in live.get(None, [])],
            "open_keys": open_keys,
            "total_keys": len(keys),
            "backends": sorted(status.backends.items()),
        },
    )


@router.get("/audit")
async def audit_page(request: Request, db: Database = Depends(require_db)) -> Response:
    params = request.query_params
    kind = params.get("kind", "all")
    kind = kind if kind in AUDIT_KINDS else "all"
    actor = params.get("actor", "").strip()[:100]
    before_raw = params.get("before", "")
    if before_raw and not _ROW_ID.fullmatch(before_raw):
        raise BadRequest("The position in the audit log is not valid.")
    before = int(before_raw) if before_raw else None

    where, args = ["TRUE"], []
    if kind != "all":
        where.append("action LIKE %s")
        args.append(f"{kind}.%")
    if actor:
        where.append("actor ILIKE %s")
        args.append("%" + actor.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if before is not None:
        where.append("id < %s")
        args.append(before)
    rows = await db.fetch_all(
        f"""
        SELECT id, at, actor, action, target, detail
        FROM admin_audit WHERE {' AND '.join(where)}
        ORDER BY id DESC LIMIT %s
        """,
        (*args, AUDIT_PAGE + 1),
    )
    more = len(rows) > AUDIT_PAGE
    rows = rows[:AUDIT_PAGE]
    existing = {
        r["id"] for r in await db.fetch_all("SELECT id FROM api_keys")
    }
    entries = [describe(row) for row in rows]
    for entry in entries:
        entry["key_exists"] = entry["key_id"] is not None and int(entry["key_id"]) in existing
    state = {"kind": kind if kind != "all" else None, "actor": actor or None}
    return render(
        request,
        "audit.html",
        {
            "active": "audit",
            "entries": entries,
            "kinds": AUDIT_KINDS,
            "kind": kind,
            "actor": actor,
            "filtered": kind != "all" or bool(actor),
            "paged": before is not None,
            "older_href": "/audit" + query_string(state, before=rows[-1]["id"]) if more and rows else None,
            "newest_href": "/audit" + query_string(state),
            "action_names": ACTIONS,
        },
    )
