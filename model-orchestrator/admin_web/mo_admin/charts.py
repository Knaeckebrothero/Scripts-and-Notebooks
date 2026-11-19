"""Geometry for the server-rendered SVG charts.

No chart library: the templates draw plain ``<rect>`` elements from the
numbers computed here. Everything is positioned in percent of the plot, so
one SVG without a viewBox stretches to any width while its text and its
2 px gaps stay crisp. Colours are CSS classes (``s1``..``s7``, ``s0`` for
"Other"), which is what lets the charts follow the light and dark theme and
keeps the pages free of inline styles.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field

from .formatting import compact, number

OTHER = "__other__"


@dataclass(frozen=True)
class SeriesDef:
    key: Hashable
    label: str
    slot: int | str  # 1..7, 0 for "Other", or a named class such as "failed"


@dataclass
class Segment:
    slot: int | str
    y: float
    h: float


@dataclass
class Bar:
    x: float
    w: float
    title: str
    total: float
    segments: list[Segment]
    tip: str  # JSON for the hover read-out


@dataclass
class LegendItem:
    label: str
    slot: int | str
    total: float


@dataclass
class StackedBars:
    bars: list[Bar] = field(default_factory=list)
    legend: list[LegendItem] = field(default_factory=list)
    y_ticks: list[str] = field(default_factory=list)       # top to bottom
    x_ticks: list[tuple[float, str, str]] = field(default_factory=list)  # (percent, label, anchor)
    table_head: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    peak: float = 0.0
    empty: bool = True
    dense: bool = False


def nice_ceiling(value: float) -> float:
    """Smallest axis maximum >= value that splits into four clean steps."""
    if value <= 4:
        return 4.0
    exponent = math.floor(math.log10(value / 4))
    for power in (exponent, exponent + 1):
        for multiple in (1, 1.5, 2, 2.5, 3, 4, 5):
            step = multiple * 10 ** power
            if step * 4 >= value and float(step).is_integer():
                return float(step * 4)
    return float(10 ** (exponent + 2))


def axis_label(value: float, peak: float) -> str:
    return number(value) if peak < 10_000 else compact(value)


def fold_series(
    values: dict[tuple[Hashable, Hashable], float],
    slots: dict[Hashable, int],
    labels: Callable[[Hashable], str],
) -> tuple[list[SeriesDef], dict[tuple[Hashable, Hashable], float]]:
    """Give ranked groups their slot and fold everything else into "Other"."""
    present = {group for (_, group) in values}
    series = [
        SeriesDef(group, labels(group), slot)
        for group, slot in sorted(slots.items(), key=lambda item: item[1])
        if group in present
    ]
    folded: dict[tuple[Hashable, Hashable], float] = {}
    has_other = False
    for (bucket, group), value in values.items():
        if group in slots:
            folded[(bucket, group)] = value
        else:
            has_other = True
            folded[(bucket, OTHER)] = folded.get((bucket, OTHER), 0) + value
    if has_other:
        series.append(SeriesDef(OTHER, "Other", 0))
    return series, folded


def stacked_bars(
    buckets: Sequence[tuple[Hashable, str, str]],
    values: dict[tuple[Hashable, Hashable], float],
    series: Sequence[SeriesDef],
    *,
    bucket_heading: str = "Day",
    fmt: Callable[[float], str] = number,
    tick_every: int | None = None,
) -> StackedBars:
    """Build a stacked column chart.

    ``buckets`` is the full, ordered x axis as ``(key, tooltip title, axis
    label)``, including buckets without data, so gaps in the data show as
    gaps in time. ``values`` maps ``(bucket key, series key)`` to a number.
    """
    chart = StackedBars()
    count = len(buckets)
    if not count:
        return chart
    totals = [sum(values.get((key, s.key), 0) for s in series) for key, _, _ in buckets]
    chart.peak = max(totals, default=0)
    chart.empty = chart.peak <= 0
    chart.dense = count > 45
    top = nice_ceiling(chart.peak)
    chart.y_ticks = [axis_label(top * step / 4, top) for step in (4, 3, 2, 1, 0)]

    slot_width = 100 / count
    for index, (key, title, _) in enumerate(buckets):
        segments: list[Segment] = []
        rows: list[list[object]] = []
        level = 0.0
        for s in series:
            value = values.get((key, s.key), 0)
            if value <= 0:
                continue
            height = value / top * 100
            segments.append(Segment(s.slot, round(100 - level - height, 3), round(height, 3)))
            level += height
            rows.append([s.slot, s.label, fmt(value)])
        rows.reverse()  # read the tooltip top-down, like the stack
        tip = json.dumps({"t": title, "rows": rows, "total": fmt(totals[index]) if len(series) > 1 else None})
        chart.bars.append(Bar(round(index * slot_width, 4), round(slot_width, 4), title, totals[index], segments, tip))

    if tick_every is None:
        tick_every = max(1, math.ceil(count / 8))
    for index, (_, _, label) in enumerate(buckets):
        if not label:
            continue
        # Count back from the newest bucket so the latest one is always labelled.
        if (count - 1 - index) % tick_every:
            continue
        chart.x_ticks.append((round((index + 0.5) * slot_width, 3), label, "middle"))

    chart.legend = [
        LegendItem(s.label, s.slot, sum(values.get((key, s.key), 0) for key, _, _ in buckets)) for s in series
    ]
    chart.table_head = [bucket_heading] + [s.label for s in series] + (["Total"] if len(series) > 1 else [])
    for index, (key, title, _) in enumerate(buckets):
        row = [title] + [fmt(values.get((key, s.key), 0)) for s in series]
        if len(series) > 1:
            row.append(fmt(totals[index]))
        chart.table_rows.append(row)
    return chart


@dataclass
class Spark:
    bars: list[tuple[int, float, float]]  # (index, y, height) in a 0..n by 0..1 box
    count: int
    label: str


def sparkline(values: Sequence[float], label: str = "") -> Spark:
    peak = max(values, default=0)
    bars = []
    for index, value in enumerate(values):
        if peak <= 0 or value <= 0:
            continue
        height = max(0.06, value / peak)
        bars.append((index, round(1 - height, 4), round(height, 4)))
    return Spark(bars, len(values), label)


def share(value: float, whole: float) -> float:
    """Percent of ``whole``, clamped, for meter bars."""
    if whole <= 0 or value <= 0:
        return 0.0
    return round(min(100.0, value / whole * 100), 2)
