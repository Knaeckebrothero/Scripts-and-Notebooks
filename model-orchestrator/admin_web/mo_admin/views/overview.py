"""The overview page: totals, the time chart by route, the route board, top
keys and the keys that need attention."""
from __future__ import annotations

from collections.abc import Hashable
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from .. import formatting, usage
from ..charts import fold_series, share, sparkline, stacked_bars
from ..db import Database
from ..deps import get_ctx, render, require_db, require_user, utcnow
from ..keys import EXPIRING_SOON, list_keys, list_routes

router = APIRouter(dependencies=[Depends(require_user), Depends(require_db)])

RANGES = ("24h", "7d", "30d")
NEVER_USED_AFTER = timedelta(days=14)


def hour_buckets(now: datetime) -> list[datetime]:
    last = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [last - timedelta(hours=23 - i) for i in range(24)]


def day_buckets(start: Any, days: int) -> list[Any]:
    return [start + timedelta(days=i) for i in range(days)]


def attention_list(keys: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Keys someone should look at, most urgent first."""
    items: list[dict[str, Any]] = []
    for key in keys:
        code = key["status"].code
        if code == "expired":
            items.append({"key": key, "tone": "bad", "rank": 0, "why": "Expired", "when": key["expires_at"]})
        elif code == "expiring":
            items.append({"key": key, "tone": "warn", "rank": 1, "why": "Expires", "when": key["expires_at"]})
        elif code == "active" and key["never_used"] and now - key["created_at"] > NEVER_USED_AFTER:
            items.append({"key": key, "tone": "idle", "rank": 2, "why": "Never used, created", "when": key["created_at"]})
    items.sort(key=lambda item: (item["rank"], item["when"]))
    return items


async def build_overview(db: Database, ranking: usage.Ranking, code: str, now: datetime) -> dict[str, Any]:
    today = now.astimezone(timezone.utc).date()
    win = usage.window(code, today)
    first_hourly = await usage.hourly_first(db)
    note: str | None = None
    hourly_missing = False

    if code == "24h":
        hours = hour_buckets(now)
        since, until = hours[0], hours[-1] + timedelta(hours=1)
        if await usage.has_hourly(db, since, until):
            resolution = "hour"
            by_route = await usage.by_hour(db, since, until, "route")
            by_key = await usage.by_hour(db, since, until, "key")
            previous = usage.total(
                (await usage.by_hour(db, since - timedelta(hours=24), since, "route")).values()
            )
            buckets = [(h, f"{formatting.weekday(h.date())}, {h:%H}:00", f"{h:%H}:00") for h in hours]
            period = "the last 24 hours"
            tick_every: int | None = 4
        else:
            # No hourly rows: fall back to what the daily table can say about
            # the last 24 hours, which is "today and yesterday".
            resolution = "day"
            hourly_missing = True
            start = today - timedelta(days=1)
            by_route = await usage.by_day(db, start, today, "route")
            by_key = await usage.by_day(db, start, today, "key")
            previous = usage.total(
                (await usage.by_day(db, start - timedelta(days=2), start - timedelta(days=1), "route")).values()
            )
            buckets = [(d, formatting.weekday(d), formatting.day(d, year=False)) for d in day_buckets(start, 2)]
            period = "today and yesterday"
            tick_every = 1
            note = (
                "Hourly usage has not been recorded yet, so this view counts today and yesterday (UTC) "
                "from the daily totals."
            )
    else:
        resolution = "day"
        by_route = await usage.by_day(db, win.start, win.end, "route")
        by_key = await usage.by_day(db, win.start, win.end, "key")
        prev_start, prev_end = win.previous
        previous = usage.total((await usage.by_day(db, prev_start, prev_end, "route")).values())
        buckets = [
            (d, formatting.weekday(d), formatting.day(d, year=False)) for d in day_buckets(win.start, win.days)
        ]
        period = f"the last {win.days} days"
        tick_every = 1 if win.days <= 7 else None

    totals = usage.total(by_route.values())
    tokens_known = first_hourly is not None
    tokens_since = None
    if tokens_known and resolution == "day" and first_hourly.date() > buckets[0][0]:
        tokens_since = first_hourly.date()

    # --- chart by route
    slots = await ranking.slots("route", today)
    series, folded = fold_series(
        {key: cell.requests for key, cell in by_route.items()}, slots, lambda group: str(group)
    )
    chart = stacked_bars(
        buckets, folded, series,
        bucket_heading="Hour" if resolution == "hour" else "Day", tick_every=tick_every,
    )

    # --- route board
    route_rows = await list_routes(db)
    types = {r["name"]: r["type"] for r in route_rows}
    per_route = usage.by_group(by_route)
    names = sorted(set(types) | {str(g) for g in per_route}, key=str.lower)
    board = []
    for name in names:
        cell = per_route.get(name, usage.Cell())
        board.append(
            {
                "name": name,
                "type": types.get(name),
                "configured": name in types,
                "cell": cell,
                "slot": slots.get(name, 0),
                "spark": sparkline(
                    [by_route[(b[0], name)].requests if (b[0], name) in by_route else 0 for b in buckets],
                    f"Requests per {resolution}",
                ),
            }
        )
    board.sort(key=lambda r: (-r["cell"].requests, r["name"].lower()))

    # --- top keys
    keys = await list_keys(db, now)
    by_id = {k["id"]: k for k in keys}
    per_key = usage.by_group(by_key)
    active_ids: set[Hashable] = {group for group, cell in per_key.items() if cell.requests > 0}
    ranked = sorted(per_key.items(), key=lambda item: (-item[1].requests, str(item[0])))
    peak = ranked[0][1].requests if ranked else 0
    top_keys = [
        {"id": key_id, "key": by_id.get(key_id), "cell": cell, "share": share(cell.requests, peak)}
        for key_id, cell in ranked[:8]
        if cell.requests > 0
    ]

    usable = [k for k in keys if k["status"].code in ("active", "expiring")]
    return {
        "window": win,
        "range": code,
        "ranges": [(c, usage.WINDOWS[c][0]) for c in RANGES],
        "period": period,
        "resolution": resolution,
        "note": note,
        "hourly_missing": hourly_missing,
        "totals": totals,
        "changes": {
            "requests": formatting.change(totals.requests, previous.requests),
            "tokens": formatting.change(totals.tokens, previous.tokens) if tokens_known else None,
        },
        "previous": previous,
        "tokens_known": tokens_known,
        "tokens_since": tokens_since,
        "active_keys": len(active_ids),
        "usable_keys": len(usable),
        "total_keys": len(keys),
        "chart": chart,
        "board": board,
        "board_limit": 9,
        "top_keys": top_keys,
        "more_keys": max(0, len([1 for _, c in ranked if c.requests > 0]) - len(top_keys)),
        "attention": attention_list(keys, now),
        "expiring_days": EXPIRING_SOON.days,
    }


@router.get("/")
async def overview(request: Request, db: Database = Depends(require_db)) -> Response:
    ctx = get_ctx(request)
    code = request.query_params.get("range", "7d")
    if code not in RANGES:
        code = "7d"
    data = await build_overview(db, ctx.ranking, code, utcnow())
    data["router"] = await ctx.router.snapshot()
    return render(request, "overview.html", {"active": "overview", **data})
