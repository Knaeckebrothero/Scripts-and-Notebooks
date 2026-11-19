"""Usage numbers from ``usage_daily`` and ``usage_hourly``.

Two tables, two jobs:

* ``usage_daily`` is the complete request history. One increment per
  forwarded request; its ``model`` column holds the ROUTE name; its
  ``error_count`` covers every forwarded outcome except ``ok`` and
  ``client_closed``. Requests and errors come from here.
* ``usage_hourly`` is newer and may be empty or start later than the daily
  history. It adds the model id as the client sent it, the outcome, token
  counts and durations, and it also records requests the router refused
  without forwarding. Tokens, outcomes, latency and "rejected" come from
  here.

Where a breakdown only exists in the hourly table (by model, by outcome, by
hour), requests and errors are derived from it with the daily table's own
definitions, so the numbers mean the same thing everywhere.

All days and hours are UTC.
"""
from __future__ import annotations

import time as _time
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .db import Database

# Outcomes the router answers itself, without forwarding: never in usage_daily.
NOT_FORWARDED = ["unavailable", "rate_limited", "concurrency_limited"]
# Forwarded outcomes that do not count as errors.
NOT_ERRORS = ["ok", "client_closed"]

# outcome -> (label, class). Classes group the ten outcomes into four tones.
OUTCOMES: dict[str, tuple[str, str]] = {
    "ok": ("Completed", "success"),
    "client_closed": ("Client disconnected", "client"),
    "client_error": ("Rejected by backend (4xx)", "client"),
    "rate_limited": ("Rate limited", "limited"),
    "concurrency_limited": ("Concurrency limited", "limited"),
    "backend_error": ("Backend error (5xx)", "failed"),
    "unreachable": ("Backend unreachable", "failed"),
    "timeout": ("Timed out", "failed"),
    "midstream_error": ("Stream broke", "failed"),
    "unavailable": ("Model unavailable", "failed"),
}
OUTCOME_CLASSES: dict[str, str] = {
    "success": "Completed",
    "client": "Client side",
    "limited": "Limited",
    "failed": "Failed",
}

DIMENSIONS: dict[str, str] = {
    "key": "Key",
    "owner": "Owner",
    "route": "Route",
    "model": "Model",
    "outcome": "Outcome",
}
_DAILY_COLUMN = {"key": "api_key_id", "owner": "owner", "route": "model"}
_HOURLY_COLUMN = {"key": "api_key_id", "owner": "owner", "route": "route", "model": "model", "outcome": "outcome"}
HOURLY_ONLY = ("model", "outcome")


def outcome_label(code: str) -> str:
    return OUTCOMES.get(code, (code.replace("_", " ").capitalize(), "failed"))[0]


def outcome_class(code: str) -> str:
    return OUTCOMES.get(code, (code, "failed"))[1]


@dataclass
class Cell:
    """Usage of one group in one time bucket (or a sum of such cells)."""

    requests: int = 0
    errors: int = 0
    rejected: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    timed: int = 0  # requests the duration sum covers

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def error_rate(self) -> float | None:
        return self.errors / self.requests if self.requests else None

    @property
    def avg_ms(self) -> float | None:
        return self.duration_ms / self.timed if self.timed else None

    def add(self, other: "Cell") -> "Cell":
        self.requests += other.requests
        self.errors += other.errors
        self.rejected += other.rejected
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.duration_ms += other.duration_ms
        self.timed += other.timed
        return self


Series = dict[tuple[Hashable, Hashable], Cell]  # (bucket, group) -> Cell


def total(cells: Iterable[Cell]) -> Cell:
    result = Cell()
    for cell in cells:
        result.add(cell)
    return result


def by_group(series: Series) -> dict[Hashable, Cell]:
    out: dict[Hashable, Cell] = {}
    for (_, group), cell in series.items():
        out.setdefault(group, Cell()).add(cell)
    return out


def by_bucket(series: Series) -> dict[Hashable, Cell]:
    out: dict[Hashable, Cell] = {}
    for (bucket, _), cell in series.items():
        out.setdefault(bucket, Cell()).add(cell)
    return out


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


# ------------------------------------------------------------------ queries
_HOURLY_SQL = """
    SELECT {bucket} AS bucket, {column} AS grp,
           SUM(requests) AS total,
           COALESCE(SUM(requests) FILTER (WHERE outcome = ANY(%(refused)s)), 0) AS rejected,
           COALESCE(SUM(requests) FILTER (
               WHERE outcome <> ALL(%(refused)s) AND outcome <> ALL(%(fine)s)), 0) AS errors,
           SUM(prompt_tokens) AS prompt_tokens,
           SUM(completion_tokens) AS completion_tokens,
           SUM(duration_ms_sum) AS duration_all,
           COALESCE(SUM(duration_ms_sum) FILTER (WHERE outcome <> ALL(%(refused)s)), 0) AS duration_forwarded
    FROM usage_hourly
    WHERE hour >= %(since)s AND hour < %(until)s {key_filter}
    GROUP BY 1, 2
"""


async def _hourly(
    db: Database, bucket_sql: str, since: datetime, until: datetime, dim: str, key_id: int | None
) -> Series:
    rows = await db.fetch_all(
        _HOURLY_SQL.format(
            bucket=bucket_sql,
            column=_HOURLY_COLUMN[dim],
            key_filter="AND api_key_id = %(key_id)s" if key_id is not None else "",
        ),
        {"since": since, "until": until, "refused": NOT_FORWARDED, "fine": NOT_ERRORS, "key_id": key_id},
    )
    series: Series = {}
    for row in rows:
        everything = int(row["total"])
        rejected = int(row["rejected"])
        forwarded = everything - rejected
        if dim == "outcome":
            cell = Cell(
                requests=everything, errors=0, rejected=0,
                duration_ms=int(row["duration_all"]), timed=everything,
            )
        else:
            cell = Cell(
                requests=forwarded, errors=int(row["errors"]), rejected=rejected,
                duration_ms=int(row["duration_forwarded"]), timed=forwarded,
            )
        cell.prompt_tokens = int(row["prompt_tokens"])
        cell.completion_tokens = int(row["completion_tokens"])
        series[(row["bucket"], row["grp"])] = cell
    return series


async def by_hour(
    db: Database, since: datetime, until: datetime, dim: str = "route", key_id: int | None = None
) -> Series:
    """Hourly buckets, from ``usage_hourly`` alone. ``until`` is exclusive."""
    series = await _hourly(db, "hour", since, until, dim, key_id)
    return {(bucket.astimezone(timezone.utc), group): cell for (bucket, group), cell in series.items()}


async def by_day(
    db: Database, start: date, end: date, dim: str = "route", key_id: int | None = None
) -> Series:
    """Daily buckets for ``start``..``end`` inclusive.

    For key, owner and route, requests and errors are the ``usage_daily``
    numbers and the hourly table only contributes tokens, durations and
    rejected requests. For model and outcome everything is hourly.
    """
    hourly = await _hourly(
        db, "(hour AT TIME ZONE 'UTC')::date", day_start(start), day_start(end + timedelta(days=1)), dim, key_id
    )
    if dim in HOURLY_ONLY:
        return hourly

    rows = await db.fetch_all(
        f"""
        SELECT day AS bucket, {_DAILY_COLUMN[dim]} AS grp,
               SUM(request_count) AS requests, SUM(error_count) AS errors
        FROM usage_daily
        WHERE day BETWEEN %(start)s AND %(end)s {"AND api_key_id = %(key_id)s" if key_id is not None else ""}
        GROUP BY 1, 2
        """,
        {"start": start, "end": end, "key_id": key_id},
    )
    series: Series = {}
    for row in rows:
        series[(row["bucket"], row["grp"])] = Cell(requests=int(row["requests"]), errors=int(row["errors"]))
    for key, extra in hourly.items():
        cell = series.get(key)
        if cell is None:
            # Hourly rows without a daily row: keep what the hourly table says.
            series[key] = extra
            continue
        cell.rejected = extra.rejected
        cell.prompt_tokens = extra.prompt_tokens
        cell.completion_tokens = extra.completion_tokens
        cell.duration_ms = extra.duration_ms
        cell.timed = extra.timed
    return series


async def hourly_first(db: Database) -> datetime | None:
    """Start of the hourly history, or ``None`` when the table is empty."""
    row = await db.fetch_one("SELECT MIN(hour) AS first FROM usage_hourly")
    first = row["first"] if row else None
    return first.astimezone(timezone.utc) if first else None


async def has_hourly(db: Database, since: datetime, until: datetime, key_id: int | None = None) -> bool:
    row = await db.fetch_one(
        "SELECT EXISTS (SELECT 1 FROM usage_hourly WHERE hour >= %(since)s AND hour < %(until)s"
        + (" AND api_key_id = %(key_id)s" if key_id is not None else "")
        + ") AS present",
        {"since": since, "until": until, "key_id": key_id},
    )
    return bool(row and row["present"])


# ------------------------------------------------------------------ colours
MAX_SERIES = 7  # named series per chart; the rest fold into "Other"


class Ranking:
    """Which groups get their own chart colour.

    Colour follows the entity, not its rank in the chart at hand: the slots
    come from one ranking over the last 90 days, so a route keeps its colour
    on every page and under every filter. Cached briefly per dimension.
    """

    TTL = 60.0

    def __init__(self, db: Database):
        self._db = db
        self._cache: dict[str, tuple[float, dict[Hashable, int]]] = {}

    async def slots(self, dim: str, today: date) -> dict[Hashable, int]:
        cached = self._cache.get(dim)
        now = _time.monotonic()
        if cached and now - cached[0] < self.TTL:
            return cached[1]
        since = today - timedelta(days=89)
        if dim in HOURLY_ONLY:
            rows = await self._db.fetch_all(
                f"""
                SELECT {_HOURLY_COLUMN[dim]} AS grp, SUM(requests) AS n
                FROM usage_hourly WHERE hour >= %s GROUP BY 1 ORDER BY n DESC, 1 LIMIT %s
                """,
                (day_start(since), MAX_SERIES),
            )
        else:
            rows = await self._db.fetch_all(
                f"""
                SELECT {_DAILY_COLUMN[dim]} AS grp, SUM(request_count) AS n
                FROM usage_daily WHERE day >= %s GROUP BY 1 ORDER BY n DESC, 1 LIMIT %s
                """,
                (since, MAX_SERIES),
            )
        slots = {row["grp"]: index + 1 for index, row in enumerate(rows)}
        self._cache[dim] = (now, slots)
        return slots

    def clear(self) -> None:
        self._cache.clear()


# ------------------------------------------------------------------- windows
@dataclass(frozen=True)
class Window:
    code: str
    label: str
    days: int
    start: date
    end: date

    @property
    def previous(self) -> tuple[date, date]:
        return self.start - timedelta(days=self.days), self.start - timedelta(days=1)


WINDOWS = {"24h": ("24 h", 1), "7d": ("7 d", 7), "30d": ("30 d", 30), "90d": ("90 d", 90)}


def window(code: str, today: date) -> Window:
    label, days = WINDOWS[code]
    return Window(code, label, days, today - timedelta(days=days - 1), today)


def key_names(keys: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {k["id"]: k for k in keys}
