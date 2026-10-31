"""The house's recent nights, replayed against the battery as it is now.

Instead of modelling the house, the plan asks what would happen if tonight used
power the way each of the last nights actually did. Cooking, the dishwasher and a
cold night's heat pump are all already in those nights, metered or not.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

import numpy as np

from .batch import Floats
from .battery import Rate

# The most a house plausibly uses in one hour; more is a counter glitch.
MAX_HOURLY_KWH = 15.0

# kWh used in each local clock hour, keyed by (local date, hour).
type HourlyUse = Mapping[tuple[date, int], float]


def hourly_use(
    rows: Mapping[datetime, float], tz: tzinfo
) -> dict[tuple[date, int], float]:
    """Key the recorder's hourly kWh by local date and clock hour.

    An hour seen twice (the autumn clock change) holds both; implausible values
    are left out rather than learned.
    """
    use: dict[tuple[date, int], float] = {}
    for start, kwh in rows.items():
        if not 0 <= kwh <= MAX_HOURLY_KWH:
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


def typical_by_hour(use: HourlyUse) -> dict[int, float]:
    """The median kWh of each clock hour over all days given."""
    by_hour: dict[int, list[float]] = {}
    for (_, hour), kwh in use.items():
        by_hour.setdefault(hour, []).append(kwh)
    return {hour: statistics.median(values) for hour, values in by_hour.items()}


def past_days(use: HourlyUse, tz: tzinfo, count: int) -> list[PastDay]:
    """The last *count* days, each ready to stand in for the hours ahead.

    An hour the recorder missed takes that hour's median, so a gap does not read
    as a night without load.
    """
    fallback = typical_by_hour(use)
    return [PastDay(n, use, tz, fallback) for n in range(1, count + 1)]


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


# Hours before now compared with the same hours on each past day.
SIMILARITY_HOURS = 6
# A day this many "typical differences" away from today weighs e^-1 as much.
MIN_BANDWIDTH = 1e-3
# A past day of the other kind (weekday, weekend) counts this much.
OTHER_DAY_TYPE = 0.25

# Any hourly series keyed by (local date, clock hour): kWh, °C, people home.
type HourlySeries = Mapping[tuple[date, int], float]


def _window(now: datetime, tz: tzinfo) -> list[datetime]:
    """The local hours just before now, oldest first, ending with the current one."""
    hour = now.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    return [hour - timedelta(hours=back) for back in range(SIMILARITY_HOURS, -1, -1)]


def _kernel(
    series: HourlySeries,
    hours: list[datetime],
    days: Sequence[PastDay],
    current: float | None = None,
) -> Floats | None:
    """How alike each past day's hours are to today's: 1 for identical, falling
    off with the root-mean-square difference, scaled by the median difference.
    None when today has nothing to compare."""
    today = np.array(
        [series.get((h.date(), h.hour), np.nan) for h in hours[:-1]]
        + [np.nan if current is None else current]
    )
    if np.all(np.isnan(today)):
        return None
    past = np.array(
        [
            [
                series.get((h.date() - timedelta(days=d.days_ago), h.hour), np.nan)
                for h in hours
            ]
            for d in days
        ]
    )
    diff = past - today
    seen = ~np.isnan(diff)
    counts = seen.sum(axis=1)
    with np.errstate(invalid="ignore"):
        distance = np.sqrt(np.where(seen, diff**2, 0.0).sum(axis=1) / counts)
    known = counts > 0
    if not known.any():
        return None
    bandwidth = max(float(np.median(distance[known])), MIN_BANDWIDTH)
    return np.where(known, np.exp(-((distance / bandwidth) ** 2)), math.exp(-1))


def _is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def similarity_weights(
    use: HourlyUse,
    tz: tzinfo,
    now: datetime,
    days: Sequence[PastDay],
    partial_kw: float | None = None,
    *,
    by_use: bool = True,
    by_day_type: bool = False,
    others: Sequence[HourlySeries] = (),
) -> list[float]:
    """Weigh each past day by how much the hours before *now* resemble today.

    Today's last hours are compared with the same clock hours on each past day,
    in each series asked for: the house's use (with the hour in progress as
    *partial_kw*), and any *others* such as outdoor temperature or people home.
    The resemblances multiply. With *by_day_type*, days of the other kind
    (weekday or weekend, by the evening the night belongs to) count less.
    Nothing to compare means nothing is preferred: all weigh the same.
    """
    weights = np.ones(len(days))
    hours = _window(now, tz)
    if by_use and (kernel := _kernel(use, hours, days, partial_kw)) is not None:
        weights *= kernel
    for series in others:
        if (kernel := _kernel(series, hours, days)) is not None:
            weights *= kernel
    if by_day_type:
        local = now.astimezone(tz)
        evening = local.date() if local.hour >= 12 else local.date() - timedelta(days=1)
        weekend = _is_weekend(evening)
        weights *= np.array(
            [
                1.0
                if _is_weekend(evening - timedelta(days=d.days_ago)) == weekend
                else OTHER_DAY_TYPE
                for d in days
            ]
        )
    return [float(w) for w in weights]
