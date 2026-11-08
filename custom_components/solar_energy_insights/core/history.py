"""Views of the history for the dashboard: the house's typical week, how the
sun did against the morning's forecast, and whether the battery is the right
size for how the house uses it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from .achievements.facts import DayFacts
from .nights import HourlyUse

WEEK_DAYS = 35
# A day's ratio is left out when the forecast was too small to judge by.
MIN_FORECAST_KWH = 1.0
RATIO_DAYS = 30
SIZING_DAYS = 60
MIN_FULL_DAYS = 5
TOO_SMALL_SHARE = 0.40
SOMETIMES_SHARE = 0.15


def typical_week(use: HourlyUse, today: date, days: int = WEEK_DAYS) -> list[float]:
    """The house's mean use for each hour of the week, Monday 0:00 first: 168
    values in kWh, each over the last *days* days before today. An hour never
    seen is the mean of the others."""
    sums, counts = [0.0] * 168, [0] * 168
    first = today - timedelta(days=days)
    for (day, hour), kwh in use.items():
        if first <= day < today:
            k = day.weekday() * 24 + hour
            sums[k] += kwh
            counts[k] += 1
    means = [s / n for s, n in zip(sums, counts, strict=True) if n]
    fallback = sum(means) / len(means) if means else 0.0
    return [
        round(s / n, 3) if n else round(fallback, 3)
        for s, n in zip(sums, counts, strict=True)
    ]


@dataclass(frozen=True, slots=True)
class DayRatio:
    day: date
    forecast_kwh: float
    solar_kwh: float

    @property
    def ratio(self) -> float:
        return self.solar_kwh / self.forecast_kwh


def day_ratios(facts: Sequence[DayFacts]) -> list[DayRatio]:
    """Each closed day's solar against its morning forecast, oldest first."""
    return [
        DayRatio(f.day, f.forecast_kwh, f.solar_kwh)
        for f in sorted(facts, key=lambda f: f.day)
        if f.forecast_kwh is not None
        and f.forecast_kwh >= MIN_FORECAST_KWH
        and f.solar_kwh is not None
    ]


def mean_ratio(ratios: Sequence[DayRatio]) -> float | None:
    """The energy-weighted ratio: all the sun that came over all that was
    forecast, so a dull day doesn't count as much as a bright one."""
    forecast = sum(r.forecast_kwh for r in ratios)
    return None if forecast <= 0 else sum(r.solar_kwh for r in ratios) / forecast


class DayVerdict(StrEnum):
    RIGHT = "right"
    TOO_SMALL = "too_small"
    NEVER_FULL = "never_full"


class Sizing(StrEnum):
    RIGHT_SIZE = "right_size"
    SOMETIMES_TOO_SMALL = "sometimes_too_small"
    TOO_SMALL = "too_small"
    NOT_ENOUGH_DAYS = "not_enough_days"


@dataclass(frozen=True, slots=True)
class SizedDay:
    day: date
    verdict: DayVerdict
    export_kwh: float | None
    import_kwh: float | None


def sized_days(facts: Sequence[DayFacts]) -> list[SizedDay]:
    """A verdict for each closed day whose night is known: full and lasted,
    full and ran out, or never full."""
    out = []
    for f in sorted(facts, key=lambda f: f.day):
        if f.full is None or (f.full and f.night_lasted is None):
            continue
        verdict = (
            DayVerdict.NEVER_FULL
            if not f.full
            else DayVerdict.RIGHT
            if f.night_lasted
            else DayVerdict.TOO_SMALL
        )
        out.append(SizedDay(f.day, verdict, f.export_kwh, f.import_kwh))
    return out


def sizing(days: Sequence[SizedDay]) -> tuple[Sizing, float]:
    """The verdict over the days, and the share of full days that ran out."""
    full = [d for d in days if d.verdict is not DayVerdict.NEVER_FULL]
    if len(full) < MIN_FULL_DAYS:
        return Sizing.NOT_ENOUGH_DAYS, 0.0
    share = sum(d.verdict is DayVerdict.TOO_SMALL for d in full) / len(full)
    if share >= TOO_SMALL_SHARE:
        return Sizing.TOO_SMALL, share
    if share >= SOMETIMES_SHARE:
        return Sizing.SOMETIMES_TOO_SMALL, share
    return Sizing.RIGHT_SIZE, share
