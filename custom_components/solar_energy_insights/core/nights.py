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
