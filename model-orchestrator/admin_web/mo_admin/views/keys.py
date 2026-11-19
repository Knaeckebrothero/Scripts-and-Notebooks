"""Keys: the list, create with a show-once secret, edit, enable or disable,
delete with typed confirmation, and the per-key detail page."""
from __future__ import annotations

import re
import secrets
from datetime import timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import RedirectResponse, Response

from .. import formatting, usage
from ..charts import SeriesDef, fold_series, share, stacked_bars
from ..db import Database
from ..deps import NotFound, get_ctx, get_session, render, require_db, require_user, utcnow
from ..keys import (
    DEFAULT_DIRECTION,
    MAX_EXPIRY_AHEAD,
    Actor,
    FILTERS,
    SORTS,
    decorate,
    delete_key,
    filter_counts,
    form_values_from_row,
    get_key,
    list_keys,
    list_routes,
    parse_key_form,
    select_keys,
    set_enabled,
    create_key,
    update_key,
)
from ..security import safe_next
from .audit_text import describe
from .overview import day_buckets

router = APIRouter(dependencies=[Depends(require_user), Depends(require_db)])

FILTER_LABELS = {
    "all": "All",
    "active": "Active",
    "expiring": "Expiring soon",
    "expired": "Expired",
    "disabled": "Disabled",
    "unused": "Never used",
}
SORT_LABELS = {
    "created": "Newest first",
    "name": "Name",
    "owner": "Owner",
    "status": "Status",
    "limit": "Rate limit",
    "expires": "Expiry",
    "last_used": "Last used",
    "requests": "Requests, 30 days",
    "tokens": "Tokens, 30 days",
}
DETAIL_RANGES = ("7d", "30d", "90d")


_KEY_ID = re.compile(r"[0-9]{1,9}")


def _key_id(raw: str) -> int:
    # ASCII digits only: str.isdigit() also accepts characters such as "²",
    # which int() then refuses.
    if not _KEY_ID.fullmatch(raw):
        raise NotFound("There is no key at this address.")
    return int(raw)


async def _load(db: Database, raw_id: str) -> dict[str, Any]:
    key = await get_key(db, _key_id(raw_id))
    if key is None:
        raise NotFound("This key does not exist. It may have been deleted.")
    return key


# ----------------------------------------------------------------------- list
@router.get("/keys")
async def keys_page(request: Request, db: Database = Depends(require_db)) -> Response:
    params = request.query_params
    query = params.get("q", "").strip()[:100]
    status = params.get("status", "all")
    status = status if status in FILTERS else "all"
    sort = params.get("sort", "created")
    sort = sort if sort in SORTS else "created"
    direction = params.get("dir", "")
    direction = direction if direction in ("asc", "desc") else DEFAULT_DIRECTION[sort]

    now = utcnow()
    everything = await list_keys(db, now)
    rows = select_keys(everything, query=query, status=status, sort=sort, direction=direction)
    state = {
        "q": query or None,
        "status": status if status != "all" else None,
        "sort": sort if sort != "created" else None,
        "dir": direction if direction != DEFAULT_DIRECTION[sort] else None,
    }
    return render(
        request,
        "keys.html",
        {
            "active": "keys",
            "rows": rows,
            "total": len(everything),
            "counts": filter_counts(everything),
            "filters": [(code, FILTER_LABELS[code]) for code in FILTERS],
            "sorts": list(SORT_LABELS.items()),
            "default_direction": DEFAULT_DIRECTION,
            "query": query,
            "status": status,
            "sort": sort,
            "direction": direction,
            "state": state,
            "peak_requests": max((r["requests_30d"] for r in everything), default=0),
        },
    )


# --------------------------------------------------------------------- create
async def _form_page(
    request: Request, db: Database, *, key: dict[str, Any] | None, values: dict[str, Any],
    errors: dict[str, str], status_code: int = 200,
) -> Response:
    routes = await list_routes(db)
    names = [r["name"] for r in routes]
    stale = sorted(set((key or {}).get("allowed_models") or []) - set(names))
    today = utcnow().date()
    return render(
        request,
        "key_form.html",
        {
            "active": "keys",
            "key": key,
            "values": values,
            "errors": errors,
            "routes": routes,
            "stale_routes": stale,
            "submission_id": secrets.token_urlsafe(12),
            "today": today,
            "latest_expiry": today + MAX_EXPIRY_AHEAD,
            "presets": [(label, (today + timedelta(days=days)).isoformat())
                        for label, days in (("30 days", 30), ("90 days", 90), ("1 year", 365))],
            "refresh_seconds": get_ctx(request).settings.key_refresh_seconds,
        },
        status_code=status_code,
    )


@router.get("/keys/new")
async def new_key_page(request: Request, db: Database = Depends(require_db)) -> Response:
    values = {
        "name": "", "owner": "", "owner_email": "", "rate_limit_rpm": "100", "max_concurrency": "",
        "expires_on": "", "notes": "", "access": "all", "allowed_models": [],
    }
    return await _form_page(request, db, key=None, values=values, errors={})


@router.post("/keys/new")
async def create_key_action(
    request: Request, db: Database = Depends(require_db), actor: Actor = Depends(require_user)
) -> Response:
    ctx = get_ctx(request)
    form = await request.form()
    routes = await list_routes(db)
    data, errors, values = parse_key_form(form, route_names=[r["name"] for r in routes])
    if data is None:
        return await _form_page(request, db, key=None, values=values, errors=errors, status_code=422)

    if not ctx.submissions.first_time(str(form.get("submission_id") or "")):
        # The browser re-sent a form that already created its key.
        get_session(request).flash(
            "warn", "That form was already submitted. The key exists; its secret cannot be shown again."
        )
        return RedirectResponse("/keys", status_code=303)

    key_id, token = await create_key(db, data, actor)
    ctx.ranking.clear()
    key = decorate(await get_key(db, key_id) or {}, utcnow())
    return render(
        request,
        "key_created.html",
        {
            "active": "keys",
            "key": key,
            "token": token,
            "refresh_seconds": ctx.settings.key_refresh_seconds,
        },
        status_code=201,
    )


# ----------------------------------------------------------------------- edit
@router.get("/keys/{key_id}/edit")
async def edit_key_page(request: Request, key_id: str, db: Database = Depends(require_db)) -> Response:
    key = await _load(db, key_id)
    return await _form_page(request, db, key=key, values=form_values_from_row(key), errors={})


@router.post("/keys/{key_id}/edit")
async def edit_key_action(
    request: Request, key_id: str, db: Database = Depends(require_db), actor: Actor = Depends(require_user)
) -> Response:
    key = await _load(db, key_id)
    form = await request.form()
    routes = await list_routes(db)
    data, errors, values = parse_key_form(
        form,
        route_names=[r["name"] for r in routes],
        keep_routes=list(key.get("allowed_models") or []),
        keep_expiry=key.get("expires_at"),
    )
    if data is None:
        return await _form_page(request, db, key=key, values=values, errors=errors, status_code=422)
    changes = await update_key(db, key["id"], data, actor)
    if changes is None:
        raise NotFound("This key was deleted while you were editing it.")
    session = get_session(request)
    if changes:
        session.flash("ok", f"Saved changes to {data.name}.")
    else:
        session.flash("info", "Nothing changed.")
    return RedirectResponse(f"/keys/{key['id']}", status_code=303)


# ------------------------------------------------------------ enable/disable
@router.post("/keys/{key_id}/enabled")
async def set_enabled_action(
    request: Request, key_id: str, db: Database = Depends(require_db), actor: Actor = Depends(require_user)
) -> Response:
    form = await request.form()
    wanted = str(form.get("enabled") or "")
    if wanted not in ("0", "1"):
        raise NotFound("Say whether the key should be enabled or disabled.")
    row = await set_enabled(db, _key_id(key_id), wanted == "1", actor)
    if row is None:
        raise NotFound("This key does not exist. It may have been deleted.")
    seconds = get_ctx(request).settings.key_refresh_seconds
    verb = "Enabled" if wanted == "1" else "Disabled"
    get_session(request).flash("ok", f"{verb} {row['name']}. The router picks this up within {seconds} seconds.")
    back = safe_next(str(form.get("return_to") or ""))
    return RedirectResponse(back if back != "/" else f"/keys/{row['id']}", status_code=303)


# --------------------------------------------------------------------- delete
@router.get("/keys/{key_id}/delete")
async def delete_key_page(request: Request, key_id: str, db: Database = Depends(require_db)) -> Response:
    key = decorate(await _load(db, key_id), utcnow())
    return render(
        request, "key_delete.html",
        {"active": "keys", "key": key, "error": None, "typed": "",
         "refresh_seconds": get_ctx(request).settings.key_refresh_seconds},
    )


@router.post("/keys/{key_id}/delete")
async def delete_key_action(
    request: Request, key_id: str, db: Database = Depends(require_db), actor: Actor = Depends(require_user)
) -> Response:
    key = decorate(await _load(db, key_id), utcnow())
    form = await request.form()
    typed = str(form.get("confirm_name") or "").strip()
    if typed != key["name"]:
        return render(
            request, "key_delete.html",
            {"active": "keys", "key": key, "typed": typed,
             "error": "That does not match the key's name. Nothing was deleted.",
             "refresh_seconds": get_ctx(request).settings.key_refresh_seconds},
            status_code=422,
        )
    deleted = await delete_key(db, key["id"], actor)
    if deleted is None:
        raise NotFound("This key was already deleted.")
    get_ctx(request).ranking.clear()
    get_session(request).flash("ok", f"Deleted {deleted['name']}. Its usage history is kept.")
    return RedirectResponse("/keys", status_code=303)


# --------------------------------------------------------------------- detail
@router.get("/keys/{key_id}")
async def key_detail(request: Request, key_id: str, db: Database = Depends(require_db)) -> Response:
    ctx = get_ctx(request)
    now = utcnow()
    key = decorate(await _load(db, key_id), now)
    code = request.query_params.get("range", "30d")
    if code not in DETAIL_RANGES:
        code = "30d"
    today = now.astimezone(timezone.utc).date()
    win = usage.window(code, today)

    by_route = await usage.by_day(db, win.start, win.end, "route", key["id"])
    by_model = await usage.by_day(db, win.start, win.end, "model", key["id"])
    by_outcome = await usage.by_day(db, win.start, win.end, "outcome", key["id"])
    totals = usage.total(by_route.values())
    first_hourly = await usage.hourly_first(db)

    days = day_buckets(win.start, win.days)
    buckets = [(d, formatting.weekday(d), formatting.day(d, year=False)) for d in days]
    slots = await ctx.ranking.slots("route", today)
    series, folded = fold_series({k: c.requests for k, c in by_route.items()}, slots, str)
    requests_chart = stacked_bars(buckets, folded, series, tick_every=1 if win.days <= 7 else None)

    token_values: dict[tuple[Any, Any], float] = {}
    for (bucket, _), cell in by_route.items():
        token_values[(bucket, "prompt")] = token_values.get((bucket, "prompt"), 0) + cell.prompt_tokens
        token_values[(bucket, "completion")] = token_values.get((bucket, "completion"), 0) + cell.completion_tokens
    tokens_chart = stacked_bars(
        buckets, token_values,
        [SeriesDef("prompt", "Prompt tokens", "prompt"), SeriesDef("completion", "Completion tokens", "completion")],
        fmt=formatting.compact, tick_every=1 if win.days <= 7 else None,
    )

    routes = sorted(usage.by_group(by_route).items(), key=lambda item: (-item[1].requests, str(item[0])))
    models = sorted(usage.by_group(by_model).items(), key=lambda item: (-item[1].requests, str(item[0])))
    outcomes = sorted(usage.by_group(by_outcome).items(), key=lambda item: (-item[1].requests, str(item[0])))
    outcome_total = sum(cell.requests for _, cell in outcomes)
    classes: dict[str, int] = {}
    for code_, cell in outcomes:
        cls = usage.outcome_class(str(code_))
        classes[cls] = classes.get(cls, 0) + cell.requests
    segments, offset = [], 0.0
    for cls in usage.OUTCOME_CLASSES:
        width = share(classes.get(cls, 0), outcome_total)
        if width > 0:
            segments.append({"cls": cls, "x": round(offset, 2), "w": width,
                             "label": usage.OUTCOME_CLASSES[cls], "count": classes[cls]})
            offset += width

    history = await db.fetch_all(
        "SELECT id, at, actor, action, target, detail FROM admin_audit WHERE target = %s ORDER BY id DESC LIMIT 8",
        (f"key:{key['id']}",),
    )
    route_names = {r["name"] for r in await list_routes(db)}
    return render(
        request,
        "key_detail.html",
        {
            "active": "keys",
            "key": key,
            "range": code,
            "ranges": [(c, usage.WINDOWS[c][0]) for c in DETAIL_RANGES],
            "window": win,
            "totals": totals,
            "tokens_known": first_hourly is not None,
            "tokens_since": first_hourly.date() if first_hourly and first_hourly.date() > win.start else None,
            "requests_chart": requests_chart,
            "tokens_chart": tokens_chart,
            "routes": [{"name": g, "cell": c, "slot": slots.get(g, 0),
                        "share": share(c.requests, routes[0][1].requests if routes else 0)} for g, c in routes],
            "models": [{"name": g, "cell": c,
                        "share": share(c.requests, models[0][1].requests if models else 0)} for g, c in models],
            "outcomes": [{"code": g, "cell": c, "share": share(c.requests, outcome_total)} for g, c in outcomes],
            "outcome_total": outcome_total,
            "outcome_segments": segments,
            "history": [describe(row) for row in history],
            "stale_routes": sorted(set(key.get("allowed_models") or []) - route_names),
            "refresh_seconds": ctx.settings.key_refresh_seconds,
        },
    )
