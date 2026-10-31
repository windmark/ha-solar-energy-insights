"""A grid of situations the plan must handle: seasons, skies, houses, battery
levels and times of day, built the same way every time."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from custom_components.solar_energy_insights.core.battery import Battery
from custom_components.solar_energy_insights.core.forecast import PvForecast, Slot
from custom_components.solar_energy_insights.core.plan import PlanInputs
from tests.core.helpers import TZ, at

type Use = dict[tuple[date, int], float]


@dataclass(frozen=True)
class Season:
    name: str
    day: date
    rise: tuple[int, int]
    set: tuple[int, int]

    def sunrise_after(self, now: datetime) -> datetime:
        return self._next(now, self.rise)

    def sunset_after(self, now: datetime) -> datetime:
        return self._next(now, self.set)

    @staticmethod
    def _next(now: datetime, hm: tuple[int, int]) -> datetime:
        local = now.astimezone(TZ)
        candidate = at(local.date(), *hm)
        return (
            candidate if candidate > now else at(local.date() + timedelta(days=1), *hm)
        )


SEASONS = (
    Season("summer", date(2026, 6, 10), (5, 15), (21, 20)),
    Season("winter", date(2026, 12, 10), (8, 10), (16, 25)),
)


def sky(season: Season, peak_kw: float) -> PvForecast:
    """Solcast half hours between sunrise and sunset, today and tomorrow."""
    slots = []
    for day in (season.day, season.day + timedelta(days=1)):
        start, end = at(day, *season.rise), at(day, *season.set)
        count = int((end - start).total_seconds() // 1800)
        for k in range(count):
            kw = peak_kw * math.sin(math.pi * (k + 0.5) / count)
            slots.append(
                Slot(start + timedelta(minutes=30 * k), kw, kw * 0.5, kw * 1.1)
            )
    return PvForecast(slots)


SKIES = {"sunny": 9.0, "dull": 1.5, "none": 0.0}


def flat(_n: int, _hour: int) -> float:
    return 0.4


def heavy_evenings(_n: int, hour: int) -> float:
    return 1.4 if 17 <= hour < 23 else 0.35


def erratic(n: int, hour: int) -> float:
    """Different every day and hour, the same on every run."""
    return round(0.2 + ((n * 7919 + hour * 104729) % 97) / 97 * 1.8, 3)


HOUSES: dict[str, Callable[[int, int], float]] = {
    "flat": flat,
    "heavy evenings": heavy_evenings,
    "erratic": erratic,
}


def history(day: date, kwh_at: Callable[[int, int], float], count: int = 14) -> Use:
    return {
        (day - timedelta(days=n), hour): kwh_at(n, hour)
        for n in range(1, count + 1)
        for hour in range(24)
    }


BATTERY = Battery(capacity_kwh=15.0, soc_pct=50.0, floor_pct=8.0)
# Every three hours, and the minutes around sunset and midnight.
HOURS = (0, 3, 6, 9, 12, 15, 18, 21)


def moments(season: Season) -> list[datetime]:
    sunset = at(season.day, *season.set)
    midnight = at(season.day + timedelta(days=1), 0)
    return [at(season.day, h) for h in HOURS] + [
        sunset - timedelta(minutes=5),
        sunset + timedelta(minutes=5),
        midnight - timedelta(minutes=5),
        midnight + timedelta(minutes=5),
    ]


def inputs(
    season: Season,
    now: datetime,
    *,
    peak_kw: float,
    use: Use,
    soc: float,
    nights: int = 7,
    **kwargs: Any,
) -> PlanInputs:
    return PlanInputs(
        now=now,
        tz=TZ,
        sunrise=season.sunrise_after(now),
        sunset=season.sunset_after(now),
        battery=Battery(
            capacity_kwh=BATTERY.capacity_kwh, soc_pct=soc, floor_pct=BATTERY.floor_pct
        ),
        pv=sky(season, peak_kw),
        use=use,
        nights=nights,
        **kwargs,
    )
