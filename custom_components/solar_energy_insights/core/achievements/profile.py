"""What a home can measure, and its size, so one catalogue fits every home."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from .facts import DayFacts

# Near the equator, seasons hardly differ: seasonal badges are not offered.
TROPICS_DEG = 15.0


class Capability(StrEnum):
    BATTERY = "battery"
    HOUSE = "house"
    SOLAR = "solar"
    GRID = "grid"
    FORECAST = "forecast"
    ADVICE = "advice"
    PEAK = "peak"
    CYCLES = "cycles"
    # The array's size is known (from Solcast's sites or set by hand).
    ARRAY = "array"
    SEASONS = "seasons"


class Season(StrEnum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


@dataclass(frozen=True, slots=True)
class Profile:
    capabilities: frozenset[Capability]
    kwp: float | None
    battery_kwh: float
    latitude: float
    # A year's yield per kWp against an ideal roof's, from where the planes face.
    relative_yield: float = 1.0

    @property
    def southern(self) -> bool:
        return self.latitude < 0

    def has(self, *needs: Capability) -> bool:
        return all(n in self.capabilities for n in needs)


def profile_of(
    facts: Sequence[DayFacts],
    battery_kwh: float,
    latitude: float,
    kwp: float | None = None,
    relative_yield: float = 1.0,
) -> Profile:
    """What the history shows this home can measure, with the array as configured."""
    caps = {Capability.BATTERY, Capability.HOUSE}
    if any(f.solar_kwh is not None for f in facts):
        caps.add(Capability.SOLAR)
    if any(f.import_kwh is not None for f in facts) and any(
        f.export_kwh is not None for f in facts
    ):
        caps.add(Capability.GRID)
    if any(f.forecast_kwh for f in facts):
        caps.add(Capability.FORECAST)
    if any(f.said_outlook or f.said_full is not None for f in facts):
        caps.add(Capability.ADVICE)
    if any(f.peak_kw is not None for f in facts):
        caps.add(Capability.PEAK)
    if any(f.battery_cycles is not None for f in facts):
        caps.add(Capability.CYCLES)
    if abs(latitude) >= TROPICS_DEG:
        caps.add(Capability.SEASONS)
    # The array's size is read from its configuration, never guessed from output.
    if kwp:
        caps.add(Capability.ARRAY)
    return Profile(frozenset(caps), kwp or None, battery_kwh, latitude, relative_yield)


# Astronomical dates, close enough for badges: equinoxes and solstices.
_SPRING, _SUMMER, _AUTUMN, _WINTER = (3, 20), (6, 21), (9, 22), (12, 21)


def season_of(day: date, southern: bool = False) -> Season:
    md = (day.month, day.day)
    if _SPRING <= md < _SUMMER:
        north = Season.SPRING
    elif _SUMMER <= md < _AUTUMN:
        north = Season.SUMMER
    elif _AUTUMN <= md < _WINTER:
        north = Season.AUTUMN
    else:
        north = Season.WINTER
    if not southern:
        return north
    return {
        Season.SPRING: Season.AUTUMN,
        Season.SUMMER: Season.WINTER,
        Season.AUTUMN: Season.SPRING,
        Season.WINTER: Season.SUMMER,
    }[north]


def _nearest(day: date, month_day: tuple[int, int]) -> int:
    """Days to the nearest occurrence of a month and day, either way."""
    candidates = [date(day.year + k, *month_day) for k in (-1, 0, 1)]
    return min(abs((day - c).days) for c in candidates)


def winter_solstice(southern: bool) -> tuple[int, int]:
    return _SUMMER if southern else _WINTER


def summer_solstice(southern: bool) -> tuple[int, int]:
    return _WINTER if southern else _SUMMER


def days_from_winter_solstice(day: date, southern: bool) -> int:
    return _nearest(day, winter_solstice(southern))


def days_from_summer_solstice(day: date, southern: bool) -> int:
    return _nearest(day, summer_solstice(southern))


def days_before_winter_solstice(day: date, southern: bool) -> int:
    """How many days until the next winter solstice."""
    md = winter_solstice(southern)
    this = date(day.year, *md)
    nxt = this if this >= day else date(day.year + 1, *md)
    return (nxt - day).days


def is_equinox(day: date) -> bool:
    return (day.month, day.day) in (_SPRING, _AUTUMN)


def next_date(day: date, month_day: tuple[int, int]) -> date:
    this = date(day.year, *month_day)
    return this if this >= day else date(day.year + 1, *month_day)


def window_opens(day: date, month_day: tuple[int, int], before: int) -> date:
    """When the next window of *before* days ahead of a date opens."""
    start = next_date(day, month_day) - timedelta(days=before)
    if start <= day <= next_date(day, month_day):
        return day
    return start
