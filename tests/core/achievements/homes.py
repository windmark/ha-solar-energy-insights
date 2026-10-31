"""Made-up homes for the achievements tests: any array, any battery, any sky."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

from custom_components.solar_energy_insights.core.achievements.facts import DayFacts
from tests.core.helpers import TZ


def sunshine(day: date, southern: bool = False) -> float:
    """0 to 1: long sunny days around midsummer, short dull ones in winter, with
    a weather pattern that repeats every 11 days."""
    doy = day.timetuple().tm_yday + (182 if southern else 0)
    season = 0.55 + 0.45 * math.cos((doy - 172) / 365 * 2 * math.pi)
    weather = (0.35, 1.0, 0.8, 0.95, 0.5, 1.0, 0.9, 0.2, 0.85, 1.0, 0.7)
    return season * weather[day.toordinal() % len(weather)]


def home(
    days: int,
    end: date,
    kwp: float = 10.0,
    battery_kwh: float = 10.0,
    house_kwh: float = 9.0,
    southern: bool = False,
    tweak: Callable[[DayFacts], DayFacts] | None = None,
    varied: bool = False,
) -> list[DayFacts]:
    """*days* closed days up to *end*, for a home of this size."""
    out = []
    cycles = 0.0
    for n in range(days, 0, -1):
        day = end - timedelta(days=n - 1)
        sun = sunshine(day, southern)
        solar = round(kwp * 5.5 * sun, 2)
        # A real house uses more on some days than others, whatever the sky.
        use = (
            house_kwh * (0.75 + 0.5 * ((day.toordinal() * 37) % 17) / 16)
            if varied
            else house_kwh
        )
        # The battery takes what the house leaves, the rest goes out.
        daytime_use = use * 0.45
        to_battery = min(max(solar - daytime_use, 0), battery_kwh * 0.9)
        export = round(max(solar - daytime_use - to_battery, 0), 2)
        night_need = use * 0.55
        imported = round(
            max(night_need - to_battery * 0.96, 0) + max(daytime_use - solar, 0), 2
        )
        full = to_battery >= battery_kwh * 0.9 - 1e-9
        # The battery's own counter: a cycle per full battery's worth charged.
        cycles += to_battery / battery_kwh
        f = DayFacts(
            day=day,
            house_kwh=round(use, 2),
            solar_kwh=solar,
            solar_best_hour_kwh=round(kwp * 0.85 * min(1.0, sun * 1.6), 2),
            import_kwh=imported,
            export_kwh=export,
            offgrid_hours=24.0 if imported < 0.02 else max(0.0, 24 - imported * 4),
            soc_max=100.0 if full else round(10 + to_battery / battery_kwh * 100, 1),
            soc_min=10.0,
            full_at=datetime(day.year, day.month, day.day, tzinfo=TZ)
            + timedelta(
                minutes=round(14 * 60 - sun * 240 + (day.toordinal() * 13) % 45)
            )
            if full
            else None,
            night_lasted=imported < 0.05,
            night_spare_kwh=round(max(to_battery * 0.96 - night_need, 0), 2),
            forecast_kwh=round(
                solar / (0.85 + 0.3 * ((day.toordinal() * 7) % 10) / 10), 2
            ),
            peak_kw=round(kwp * 0.85 * sun, 2),
            battery_cycles=round(cycles, 2),
        )
        out.append(tweak(f) if tweak else f)
    return out


def without(*names: str) -> Callable[[DayFacts], DayFacts]:
    """A home that cannot measure these facts."""
    blank: dict[str, Any] = dict.fromkeys(names)
    return lambda f: replace(f, **blank)
