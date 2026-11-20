"""Last week in one line, what yesterday was the best at in a while, and the
last seven days against the same days a year ago.

All from the day facts already kept: no new data, only a moment to notice
progress without opening the trophy room.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any, NamedTuple

from .facts import DayFacts
from .game import score

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


class Measure(NamedTuple):
    key: str
    of: Callable[[DayFacts], float | None]
    higher_is_better: bool


MEASURES: tuple[Measure, ...] = (
    Measure("best_day", lambda f: f.solar_kwh, True),
    Measure("self_sufficient", lambda f: f.self_sufficiency, True),
    Measure("thriftiest", lambda f: f.house_kwh, False),
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


# 52 weeks: the same weekdays a year ago, so weekends line up with weekends.
YEAR_DAYS = 364
WEEK_DAYS = 7
# Days each week needs before the two are compared.
MIN_WEEK_DAYS = 5


def _ratio(week: Sequence[DayFacts]) -> float | None:
    """Self-sufficiency over the week, weighted by each day's use, each day
    from sunrise as the day's own measure."""
    rows = [(f.day_own_import_kwh, f.day_house_kwh) for f in week]
    known = [(i, h) for i, h in rows if i is not None]
    use = sum(h for _, h in known)
    if not known or use <= 0:
        return None
    return round(max(0.0, 1 - sum(i or 0.0 for i, _ in known) / use), 3)


def _total(
    week: Sequence[DayFacts], of: Callable[[DayFacts], float | None]
) -> float | None:
    values = [v for f in week if (v := of(f)) is not None]
    return round(sum(values), 1) if len(values) >= MIN_WEEK_DAYS else None


# What the two weeks are compared on, and which way is better; None: neither.
GHOSTS: tuple[tuple[str, bool | None], ...] = (
    ("score", True),
    ("self_sufficiency", True),
    ("solar_kwh", True),
    ("nights_lasted", True),
    ("house_kwh", None),
)


def year_ago(
    days: Sequence[DayFacts], today: date, floor_pct: float = 5.0
) -> dict[str, Any] | None:
    """The last seven closed days against the same days 52 weeks earlier: a
    bar that moves with the season, so a December is held to a December.
    With the same day last year, for today's chase."""
    by_day = {f.day: f for f in days}
    until = today - timedelta(days=1)
    span = [until - timedelta(days=k) for k in range(WEEK_DAYS - 1, -1, -1)]
    # Only days kept in both years, paired, so a missing day can't pass for
    # a worse week.
    year = timedelta(days=YEAR_DAYS)
    pairs = [
        (by_day[d], by_day[d - year])
        for d in span
        if d in by_day and d - year in by_day
    ]
    if len(pairs) < MIN_WEEK_DAYS:
        return None
    now = [a for a, _ in pairs]
    then = [b for _, b in pairs]

    def measures(week: Sequence[DayFacts]) -> dict[str, float | None]:
        nights = [f.night_lasted for f in week if f.night_lasted is not None]
        # The same nights known in both years, or none compared.
        if len(nights) < len(week):
            nights = []
        return {
            "score": round(sum(score(f, floor_pct) for f in week) / len(week), 1),
            "self_sufficiency": _ratio(week),
            "solar_kwh": _total(week, lambda f: f.solar_kwh),
            "nights_lasted": float(sum(nights))
            if len(nights) >= MIN_WEEK_DAYS
            else None,
            "house_kwh": _total(week, lambda f: f.house_kwh),
        }

    a, b = measures(now), measures(then)
    rows = []
    for key, higher in GHOSTS:
        x, y = a[key], b[key]
        if x is None or y is None:
            continue
        better = None if higher is None or x == y else (x > y) == higher
        rows.append({"key": key, "now": x, "then": y, "better": better})
    same_day = by_day.get(today - timedelta(days=YEAR_DAYS))
    return {
        "from": span[0].isoformat(),
        "until": until.isoformat(),
        "then_from": (span[0] - timedelta(days=YEAR_DAYS)).isoformat(),
        "then_until": (until - timedelta(days=YEAR_DAYS)).isoformat(),
        "days": len(pairs),
        "measures": rows,
        "today_solar_kwh": None if same_day is None else same_day.solar_kwh,
    }
