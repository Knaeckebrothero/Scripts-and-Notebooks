"""The usage explorer and its CSV export."""
from __future__ import annotations

import csv
import io
from collections.abc import Hashable
from datetime import date, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from .. import formatting, usage
from ..charts import SeriesDef, fold_series, share, stacked_bars
from ..db import Database
from ..deps import BadRequest, get_ctx, query_string, render, require_db, require_user, utcnow
from ..keys import list_keys
from .overview import day_buckets

router = APIRouter(dependencies=[Depends(require_user), Depends(require_db)])

MAX_DAYS = 366
EARLIEST = date(2000, 1, 1)  # nothing was recorded before; also keeps date arithmetic in range
LATEST_AHEAD = timedelta(days=366)
PRESETS = ((7, "7 days"), (30, "30 days"), (90, "90 days"))
TABLE_LIMIT = 500


def _parse_day(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def read_filters(request: Request, today: date) -> dict[str, Any]:
    """Normalise the query string; anything odd falls back to a sane default."""
    params = request.query_params
    given_end, given_start = _parse_day(params.get("to")), _parse_day(params.get("from"))
    for given in (given_end, given_start):
        if given is not None and not EARLIEST <= given <= today + LATEST_AHEAD:
            raise BadRequest(
                f"The date {given.isoformat()} is outside the range this console keeps "
                f"({EARLIEST.isoformat()} to a year from now)."
            )
    end = given_end or today
    start = given_start or end - timedelta(days=29)
    notes: list[str] = []
    if start > end:
        start, end = end, start
        notes.append("The dates were the wrong way round, so they have been swapped.")
    if (end - start).days + 1 > MAX_DAYS:
        start = end - timedelta(days=MAX_DAYS - 1)
        notes.append(f"A range covers at most {MAX_DAYS} days; the start date was moved.")
    group = params.get("group", "key")
    group = group if group in usage.DIMENSIONS else "key"
    per_day = params.get("by") == "day"
    return {"start": start, "end": end, "group": group, "per_day": per_day, "notes": notes}


def _state(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": f["start"].isoformat(),
        "to": f["end"].isoformat(),
        "group": f["group"],
        "by": "day" if f["per_day"] else None,
    }


async def collect(db: Database, f: dict[str, Any]) -> dict[str, Any]:
    """Run the query behind both the page and the CSV."""
    series = await usage.by_day(db, f["start"], f["end"], f["group"])
    keys = {k["id"]: k for k in await list_keys(db, utcnow())} if f["group"] == "key" else {}

    def label(group: Hashable) -> str:
        if f["group"] == "key":
            key = keys.get(group)  # type: ignore[arg-type]
            return key["name"] if key else f"Deleted key #{group}"
        if f["group"] == "outcome":
            return usage.outcome_label(str(group))
        return str(group)

    groups = usage.by_group(series)
    ranked = sorted(groups.items(), key=lambda item: (-item[1].requests, -item[1].rejected, label(item[0]).lower()))
    return {"series": series, "groups": ranked, "label": label, "keys": keys, "total": usage.total(groups.values())}


@router.get("/usage")
async def usage_page(request: Request, db: Database = Depends(require_db)) -> Response:
    ctx = get_ctx(request)
    now = utcnow()
    today = now.astimezone(timezone.utc).date()
    f = read_filters(request, today)
    data = await collect(db, f)
    group, series, label = f["group"], data["series"], data["label"]
    days = (f["end"] - f["start"]).days + 1
    first_hourly = await usage.hourly_first(db)
    hourly_in_range = first_hourly is not None and first_hourly.date() <= f["end"]

    # --- chart: requests per day, stacked by group
    buckets = [(d, formatting.weekday(d), formatting.day(d, year=False)) for d in day_buckets(f["start"], days)]
    if group == "outcome":
        values: dict[tuple[Any, Any], float] = {}
        for (bucket, code), cell in series.items():
            cls = usage.outcome_class(str(code))
            values[(bucket, cls)] = values.get((bucket, cls), 0) + cell.requests
        present = {cls for (_, cls) in values}
        chart_series = [SeriesDef(c, name, c) for c, name in usage.OUTCOME_CLASSES.items() if c in present]
        slots: dict[Hashable, Any] = {}
    else:
        slots = dict(await ctx.ranking.slots(group, today))
        chart_series, values = fold_series({k: c.requests for k, c in series.items()}, slots, label)
    chart = stacked_bars(buckets, values, chart_series) if days > 1 else None

    # --- table
    peak = max((cell.requests for _, cell in data["groups"]), default=0)
    total_requests = data["total"].requests
    rows = []
    for g, cell in data["groups"]:
        key = data["keys"].get(g) if group == "key" else None
        rows.append(
            {
                "group": g,
                "label": label(g),
                "key": key,
                "cell": cell,
                "meter": share(cell.requests, peak),
                "share": cell.requests / total_requests if total_requests else None,
                "slot": usage.outcome_class(str(g)) if group == "outcome" else slots.get(g, 0),
            }
        )

    daily_rows = []
    if f["per_day"]:
        for (bucket, g), cell in sorted(
            series.items(), key=lambda item: (item[0][0], -item[1].requests, label(item[0][1]).lower()), reverse=False
        ):
            daily_rows.append({"day": bucket, "group": g, "label": label(g), "cell": cell,
                               "key": data["keys"].get(g) if group == "key" else None})
        daily_rows.sort(key=lambda r: r["day"], reverse=True)

    state = _state(f)
    return render(
        request,
        "usage.html",
        {
            "active": "usage",
            "f": f,
            "state": state,
            "dimensions": usage.DIMENSIONS,
            "group_label": usage.DIMENSIONS[group],
            "presets": [
                (text, query_string(state, **{"from": (today - timedelta(days=n - 1)).isoformat(), "to": today.isoformat()}),
                 f["end"] == today and days == n)
                for n, text in PRESETS
            ],
            "days": days,
            "today": today,
            "rows": rows,
            "daily_rows": daily_rows[:TABLE_LIMIT],
            "daily_hidden": max(0, len(daily_rows) - TABLE_LIMIT),
            "total": data["total"],
            "chart": chart,
            "hourly_only": group in usage.HOURLY_ONLY,
            "hourly_in_range": hourly_in_range,
            "tokens_known": first_hourly is not None,
            "tokens_since": first_hourly.date() if first_hourly and first_hourly.date() > f["start"] else None,
            "csv_href": "/usage.csv" + query_string(state),
        },
    )


def _csv_cell(value: Any) -> Any:
    """Neutralise spreadsheet formulas: text starting with = + - @ is quoted."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@router.get("/usage.csv")
async def usage_csv(request: Request, db: Database = Depends(require_db)) -> Response:
    today = utcnow().astimezone(timezone.utc).date()
    f = read_filters(request, today)
    data = await collect(db, f)
    group, label = f["group"], data["label"]

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    head = (["day"] if f["per_day"] else ["from", "to"]) + [group]
    if group == "key":
        head += ["key_id", "key_prefix", "owner"]
    if group == "outcome":
        head += ["outcome_code"]
    head += [
        "requests", "errors", "error_rate", "rejected", "prompt_tokens", "completion_tokens",
        "total_tokens", "avg_duration_ms",
    ]
    writer.writerow(head)

    def line(first: list[Any], g: Hashable, cell: usage.Cell) -> None:
        row: list[Any] = first + [label(g)]
        if group == "key":
            key = data["keys"].get(g)
            row += [g, key["key_prefix"] if key else "", key["owner"] if key else ""]
        if group == "outcome":
            row += [g]
        rate = cell.error_rate
        avg = cell.avg_ms
        row += [
            cell.requests,
            "" if group == "outcome" else cell.errors,
            "" if rate is None or group == "outcome" else f"{rate:.4f}",
            "" if group == "outcome" else cell.rejected,
            cell.prompt_tokens, cell.completion_tokens, cell.tokens,
            "" if avg is None else f"{avg:.0f}",
        ]
        writer.writerow([_csv_cell(v) for v in row])

    if f["per_day"]:
        for (bucket, g), cell in sorted(
            data["series"].items(), key=lambda item: (item[0][0], label(item[0][1]).lower())
        ):
            line([bucket.isoformat()], g, cell)
    else:
        for g, cell in data["groups"]:
            line([f["start"].isoformat(), f["end"].isoformat()], g, cell)

    filename = f"usage-by-{group}-{f['start'].isoformat()}-to-{f['end'].isoformat()}.csv"
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
