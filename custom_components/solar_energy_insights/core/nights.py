"""The house's recent nights, replayed against the battery as it is now.

Instead of modelling the house, the plan asks what would happen if tonight used
power the way each of the last nights actually did. Cooking, the dishwasher and a
cold night's heat pump are all already in those nights, metered or not.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

from .battery import Rate

# The most a house plausibly uses in one hour; more is a counter glitch.
MAX_HOURLY_KWH = 15.0

# kWh used in each local clock hour, keyed by (local date, hour).
type HourlyUse = Mapping[tuple[date, int], float]


# An hour's energy above this, and far above the counter's usual hour, is a
# glitch: typically a counter that first reported 0 and then its lifetime total.
JUMP_FLOOR_KWH = 25.0
JUMP_FACTOR = 10.0


def plausible(rows: Mapping[datetime, float]) -> dict[datetime, float]:
    """The counter's hourly changes without impossible ones.

    A counter that comes back after reporting 0 (or appears for the first time)
    makes the recorder count its whole total as one hour. Hours above both 25
    kWh and ten times the counter's typical hour are dropped; a big array's
    real best hours are kept.
    """
    positive = sorted(v for v in rows.values() if v > 0)
    typical_hour = positive[len(positive) // 2] if positive else 0.0
    limit = max(JUMP_FLOOR_KWH, JUMP_FACTOR * typical_hour)
    return {t: v for t, v in rows.items() if 0 <= v <= limit}


def hourly_use(
    rows: Mapping[datetime, float],
    tz: tzinfo,
    max_kwh: float | None = MAX_HOURLY_KWH,
) -> dict[tuple[date, int], float]:
    """Key the recorder's hourly kWh by local date and clock hour.

    An hour seen twice (the autumn clock change) holds both; implausible values
    are left out rather than learned. *max_kwh* None keeps any positive hour, as
    for a large solar array.
    """
    use: dict[tuple[date, int], float] = {}
    for start, kwh in rows.items():
        if kwh < 0 or (max_kwh is not None and kwh > max_kwh):
            continue
        local = start.astimezone(tz)
        key = (local.date(), local.hour)
        use[key] = use.get(key, 0.0) + kwh
    return use


@dataclass(frozen=True, slots=True)
class PastDay:
    """One past day's use, laid over the hours ahead."""

    days_ago: int
    use: HourlyUse
    tz: tzinfo
    fallback: Mapping[int, float]

    def rate(self, t: datetime) -> float:
        """kW at *t*, as the house used power that many days earlier."""
        local = t.astimezone(self.tz)
        key = (local.date() - timedelta(days=self.days_ago), local.hour)
        value = self.use.get(key)
        return value if value is not None else self.fallback.get(local.hour, 0.0)


def hourly_means(
    rows: Mapping[datetime, float], tz: tzinfo
) -> dict[tuple[date, int], float]:
    """Key the recorder's hourly means (°C, people home) by local date and hour;
    an hour seen twice (the autumn clock change) holds their average."""
    sums: dict[tuple[date, int], list[float]] = {}
    for start, value in rows.items():
        local = start.astimezone(tz)
        sums.setdefault((local.date(), local.hour), []).append(value)
    return {key: sum(v) / len(v) for key, v in sums.items()}


def typical_by_hour(use: HourlyUse) -> dict[int, float]:
    """The median kWh of each clock hour over all days given."""
    by_hour: dict[int, list[float]] = {}
    for (_, hour), kwh in use.items():
        by_hour.setdefault(hour, []).append(kwh)
    return {hour: statistics.median(values) for hour, values in by_hour.items()}


# A past day needs this many recorded hours to be replayed as a night.
MIN_DAY_HOURS = 12
# How far back to look for days with data, as a multiple of the days wanted.
LOOK_BACK = 3


def _today(use: HourlyUse) -> date | None:
    """The day the history runs up to: the last day if it is still partial,
    else the day after it."""
    if not use:
        return None
    last = max(day for day, _ in use)
    hours = sum(1 for day, _ in use if day == last)
    return last if hours < 24 else last + timedelta(days=1)


def past_days(
    use: HourlyUse, tz: tzinfo, count: int, today: date | None = None
) -> list[PastDay]:
    """The last *count* days with data, each ready to stand in for the hours ahead.

    A day the recorder mostly missed is skipped, and an older one takes its
    place: an outage is not a night. An hour missed within a day takes that
    hour's median over the days replayed, so a gap does not read as a night
    without load; older history plays no part.
    """
    today = today or _today(use)
    if today is None or count <= 0:
        return []
    hours_per_day: dict[date, int] = {}
    for day, _ in use:
        hours_per_day[day] = hours_per_day.get(day, 0) + 1
    chosen = [
        n
        for n in range(1, count * LOOK_BACK + 1)
        if hours_per_day.get(today - timedelta(days=n), 0) >= MIN_DAY_HOURS
    ][:count]
    # The usual value of each hour: from the days replayed, and for an hour none
    # of them recorded, from the recent days looked at.
    window = recent_use(use, today, range(1, count * LOOK_BACK + 1))
    fallback = typical_by_hour(window) | typical_by_hour(recent_use(use, today, chosen))
    return [PastDay(n, use, tz, fallback) for n in chosen]


def recent_use(
    use: HourlyUse, today: date, days_ago: Sequence[int]
) -> dict[tuple[date, int], float]:
    """Only the hours of the days replayed, and the small hours after each."""
    dates = {today - timedelta(days=n) for n in days_ago}
    dates |= {d + timedelta(days=1) for d in dates}
    return {key: kwh for key, kwh in use.items() if key[0] in dates and key[0] < today}


def typical(use: HourlyUse, tz: tzinfo) -> Rate:
    """The median load at each clock hour, for questions about the day ahead."""
    by_hour = typical_by_hour(use)

    def rate(t: datetime) -> float:
        return by_hour.get(t.astimezone(tz).hour, 0.0)

    return rate


def days_covered(use: HourlyUse) -> int:
    """How many distinct days have any recorded use."""
    return len({day for day, _ in use})


def quantile(values: Sequence[float], share: float) -> float:
    """The value below which *share* of *values* lie (nearest rank)."""
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


# Any hourly series keyed by (local date, clock hour): kWh, °C, people home.
type HourlySeries = Mapping[tuple[date, int], float]
