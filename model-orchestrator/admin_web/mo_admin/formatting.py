"""Number and time formatting shared by templates, charts and CSV."""
from __future__ import annotations

from datetime import date, datetime, timezone

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def number(value: float | int | None) -> str:
    if value is None:
        return "–"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


def compact(value: float | int | None) -> str:
    """1,284 / 12.9K / 4.2M / 1.31B: full below 10,000, three figures above."""
    if value is None:
        return "–"
    value = float(value)
    magnitude = abs(value)
    if magnitude < 10_000:
        return number(value)
    for limit, suffix in ((1e3, "K"), (1e6, "M"), (1e9, "B"), (1e12, "T")):
        if magnitude < limit * 1000 or suffix == "T":
            scaled = value / limit
            digits = 2 if abs(scaled) < 10 else 1 if abs(scaled) < 100 else 0
            text = f"{scaled:.{digits}f}"
            if float(text) >= 1000 and suffix != "T":
                continue  # 999,999 rounds to 1000K: say 1M instead
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return text + suffix
    return number(value)


def headline(value: float | int | None) -> str:
    """For the large figures: exact below a million, compact above."""
    if value is None:
        return "–"
    return number(value) if abs(value) < 1_000_000 else compact(value)


def percent(ratio: float | None, digits: int = 1) -> str:
    if ratio is None:
        return "–"
    value = ratio * 100
    if 0 < value < 0.1:
        return "<0.1%"
    return f"{value:.{digits}f}%"


def duration(ms: float | None) -> str:
    if ms is None:
        return "–"
    if ms < 1000:
        return f"{ms:.0f} ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f} s"
    return f"{ms / 60_000:.1f} min"


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def day(value: date | datetime | None, year: bool = True) -> str:
    if value is None:
        return "–"
    if isinstance(value, datetime):
        value = _utc(value).date()
    text = f"{value.day} {MONTHS[value.month - 1]}"
    return f"{text} {value.year}" if year else text


def weekday(value: date) -> str:
    return f"{WEEKDAYS[value.weekday()]} {value.day} {MONTHS[value.month - 1]}"


def clock(value: datetime | None) -> str:
    if value is None:
        return "–"
    value = _utc(value)
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}, {value:%H:%M}"


def iso(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return _utc(value).isoformat(timespec="seconds")
    return value.isoformat()


def _span(seconds: float) -> str:
    seconds = abs(seconds)
    if seconds < 90:
        return "a minute"
    if seconds < 3600:
        return f"{round(seconds / 60)} min"
    if seconds < 36 * 3600:
        hours = round(seconds / 3600)
        return f"{hours} h"
    days = round(seconds / 86400)
    if days < 60:
        return f"{days} days"
    if days < 720:
        return f"{round(days / 30.4)} months"
    return f"{days / 365.25:.1f} years"


def relative(value: datetime | None, now: datetime | None = None) -> str:
    """"3 h ago" for the past, "in 12 days" for the future."""
    if value is None:
        return "never"
    now = now or datetime.now(timezone.utc)
    delta = (_utc(value) - now).total_seconds()
    if abs(delta) < 45:
        return "just now"
    return f"in {_span(delta)}" if delta > 0 else f"{_span(delta)} ago"


def change(current: float, previous: float) -> tuple[str, str] | None:
    """("+12%", "up") against the previous period, or None when there is no base."""
    if previous <= 0:
        return None
    ratio = (current - previous) / previous
    if abs(ratio) < 0.005:
        return ("no change", "flat")
    sign = "+" if ratio > 0 else "−"
    magnitude = abs(ratio) * 100
    text = f"{magnitude:.0f}%" if magnitude >= 10 else f"{magnitude:.1f}%"
    if magnitude >= 1000:
        return (f"{current / previous:.0f}×", "up")
    return (f"{sign}{text}", "up" if ratio > 0 else "down")


FILTERS = {
    "num": number,
    "compact": compact,
    "headline": headline,
    "pct": percent,
    "duration": duration,
    "day": day,
    "clock": clock,
    "iso": iso,
    "relative": relative,
}
