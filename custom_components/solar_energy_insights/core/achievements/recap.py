"""Last week in one line, and what yesterday was the best at in a while.

Both from the day facts already kept: no new data, only a moment to notice
progress without opening the trophy room.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any

from .facts import DayFacts

# A day that is the best in fewer days than this is not news.
MIN_GAP_DAYS = 7
# History needed before "on record" means anything.
MIN_HISTORY_DAYS = 30


def last_week(
    days: Sequence[DayFacts],
    today: date,
    price: float | None = None,
    earned: Sequence[tuple[str, date]] = (),
) -> dict[str, Any] | None:
    """The last full week, Monday to Sunday: nights without the grid, the best
    day, the sun kept at home and what it was worth, and the badges earned."""
    monday = today - timedelta(days=today.weekday() + 7)
    sunday = monday + timedelta(days=6)
    week = [f for f in days if monday <= f.day <= sunday]
    if len(week) < 4:
        return None
    nights = [f for f in week if f.night_lasted is not None]
    sunny = [f for f in week if f.solar_kwh is not None]
    best = max(sunny, key=lambda f: f.solar_kwh or 0.0) if sunny else None
    kept = [
        (f.solar_kwh or 0.0) - (f.export_kwh or 0.0)
        for f in week
        if f.solar_kwh is not None and f.export_kwh is not None
    ]
    return {
        "from": monday.isoformat(),
        "until": sunday.isoformat(),
        "days": len(week),
        "nights": len(nights),
        "nights_lasted": sum(1 for f in nights if f.night_lasted),
        "best_day": None if best is None else best.day.isoformat(),
        "best_kwh": None if best is None else round(best.solar_kwh or 0.0, 1),
        "kept_kwh": round(sum(kept), 1) if kept else None,
        "saved": round(sum(kept) * price, 2) if kept and price is not None else None,
        "badges": sorted({name for name, on in earned if monday <= on <= sunday}),
    }


# (key, what it measures, higher is better)
MEASURES: tuple[tuple[str, Callable[[DayFacts], float | None], bool], ...] = (
    ("best_day", lambda f: f.solar_kwh, True),
    ("self_sufficient", lambda f: f.self_sufficiency, True),
    ("thriftiest", lambda f: f.house_kwh, False),
)


def best_since(
    days: Sequence[DayFacts], day: date, most: int = 2
) -> list[dict[str, Any]]:
    """What *day* was the best at in a while: for each measure, the last earlier
    day that did as well. Only gaps of a week or more, the longest first; "on
    record" when no earlier day did as well over at least a month."""
    before = sorted((f for f in days if f.day < day), key=lambda f: f.day)
    this = next((f for f in days if f.day == day), None)
    if this is None or not before:
        return []
    found: list[dict[str, Any]] = []
    for key, measure, higher in MEASURES:
        value = measure(this)
        if value is None:
            continue
        as_good = [
            f
            for f in before
            if (v := measure(f)) is not None and (v >= value if higher else v <= value)
        ]
        if as_good:
            since = as_good[-1].day
            gap = (day - since).days
            if gap >= MIN_GAP_DAYS:
                found.append({"key": key, "since": since.isoformat(), "gap_days": gap})
        elif (day - before[0].day).days >= MIN_HISTORY_DAYS:
            found.append(
                {"key": key, "since": None, "gap_days": (day - before[0].day).days}
            )
    found.sort(key=lambda x: -int(x["gap_days"]))
    return found[:most]
