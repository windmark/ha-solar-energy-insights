"""The badge catalogue: every badge, as data over the rules in rules.py.

Badges that describe how good a day was climb a ladder: each tier asks a
better day than the one before. Badges that a day either earns or not keep one
bar. Four badges are followed by a harder one, offered once they are complete.
Successors come after the badge they follow, which the evaluation relies on.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

import numpy as np

from .editions import window
from .facts import DayFacts
from .profile import (
    Capability as Cap,
)
from .profile import (
    Profile,
    Season,
    days_before_winter_solstice,
    days_from_summer_solstice,
    days_from_winter_solstice,
    is_equinox,
)
from .rules import (
    Badge,
    Context,
    Count,
    at_least,
    flat,
    in_window,
    run,
    seasonal,
    steps_met,
    top,
)

type Holds = Callable[[DayFacts], bool | None]


def _self_sufficient(share: float) -> Holds:
    def holds(f: DayFacts) -> bool | None:
        ss = f.self_sufficiency
        return None if ss is None else ss >= share

    return holds


def _perfect(f: DayFacts, _c: Context) -> bool | None:
    if f.grid_free is None or f.full is None or f.beat_forecast is None:
        return None
    return f.grid_free and f.full and f.beat_forecast


def _every_drop(f: DayFacts, c: Context) -> int | None:
    kept, sy = f.kept_at_home, c.specific_yield(f)
    median = c.quantile(c.specific_yield, 0.5, key="specific_yield")
    if kept is None or sy is None or median is None:
        return None
    return steps_met(kept, (0.95, 0.97, 0.99)) if sy >= median else 0


def _power_plant(f: DayFacts, c: Context) -> int | None:
    if f.export_kwh is None or not c.profile.kwp:
        return None
    # Per kWp of a roof facing the equator; a roof facing away needs less.
    per_kwp = f.export_kwh / c.profile.kwp / c.profile.relative_yield
    return steps_met(per_kwp, (2.0, 2.5, 3.0))


def _silver_lining(f: DayFacts, c: Context) -> int | None:
    if f.solar_kwh is None or not f.forecast_kwh:
        return None
    gloomy = c.quantile(lambda d: d.forecast_kwh, 0.2, c.season(f.day), "forecast")
    if gloomy is None:
        return None
    if f.forecast_kwh > gloomy:
        return 0
    return steps_met(f.solar_kwh / f.forecast_kwh, (1.5, 2.0, 2.5))


def _night_with_spare(f: DayFacts, c: Context) -> int | None:
    """Lasted until sunrise; then 10 % and 25 % of the battery to spare."""
    if f.night_lasted is None:
        return None
    if not f.night_lasted:
        return 0
    if f.night_spare_kwh is None or not c.profile.battery_kwh:
        return 1
    return steps_met(f.night_spare_kwh / c.profile.battery_kwh, (0.0, 0.10, 0.25))


def _autumn_night(f: DayFacts, c: Context) -> bool | None:
    if f.night_lasted is None:
        return None
    return c.season(f.day) == Season.AUTUMN and f.night_lasted


def _late_autumn(day: date, p: Profile) -> bool:
    return days_before_winter_solstice(day, p.southern) <= 42


def _midwinter(day: date, p: Profile) -> bool:
    return days_from_winter_solstice(day, p.southern) <= 10


def _midsummer(day: date, p: Profile) -> bool:
    return days_from_summer_solstice(day, p.southern) <= 10


def _sky_or_yield(f: DayFacts, c: Context) -> float | None:
    """How good the yield was for the time of year: against a clear sky where
    the planes are known, else per kWp."""
    if c.profile.has(Cap.SKY):
        return f.sky_share
    return c.specific_yield(f)


def _winter_week(f: DayFacts, c: Context) -> int | None:
    """A winter week's self-sufficiency, judged on its last day. Weeks are
    counted from the start of winter, so they don't overlap."""
    if f.self_sufficiency is None:
        return None
    found = window("winter", f.day, c.profile.southern)
    if found is None or not found[0] <= f.day <= found[1]:
        return 0
    if (f.day - found[0]).days % 7 != 6:
        return 0
    week = [f]
    for before in reversed(c.past):
        if len(week) == 7:
            break
        if (week[-1].day - before.day).days != 1 or not c.counts(before):
            return 0
        if before.self_sufficiency is None:
            return 0
        week.append(before)
    if len(week) < 7:
        return 0
    mean = sum(d.self_sufficiency or 0.0 for d in week) / 7
    return steps_met(mean, (0.8, 0.85, 0.9))


def _early_bird(f: DayFacts, c: Context) -> int | None:
    """Battery full earlier than four in five, nine in ten, nineteen in
    twenty of your days."""
    if f.full is None:
        return None
    minutes = f.full_minutes(c.tz)
    cuts = c.quantiles(
        lambda d: d.full_minutes(c.tz), (0.2, 0.1, 0.05), key="full_minutes"
    )
    if cuts is None:
        return None
    return 0 if minutes is None else steps_met(minutes, cuts, lowest=True)


def _island(f: DayFacts, _c: Context) -> int | None:
    """A stretch off the grid, counted once: on the day it ends, at its full
    length across midnights. A day off the grid all through waits for the
    stretch to end. Without stretches, a whole day off the grid."""
    if f.offgrid_stretch is None or f.offgrid_run is None:
        if f.offgrid_hours is None:
            return None
        return steps_met(f.offgrid_hours, (24, 48, 72))
    if f.offgrid_run >= 24:
        return 0
    return steps_met(f.offgrid_stretch, (24, 48, 72))


def _minutes_off(f: DayFacts) -> float | None:
    """How far the morning's "full at" was from the real time; None if it
    can't be told, infinite if one of the two never came."""
    if f.said_full is None or f.full is None:
        return None
    if f.said_full_at is None or f.full_at is None:
        return float("inf")
    return abs((f.said_full_at - f.full_at).total_seconds()) / 60


def _whisperer(f: DayFacts, _c: Context) -> int | None:
    off = _minutes_off(f)
    return None if off is None else steps_met(off, (15, 10, 5), lowest=True)


def _whisper(f: DayFacts, _c: Context) -> bool | None:
    off = _minutes_off(f)
    return None if off is None else off <= 15


def _sponge_test(f: DayFacts, c: Context) -> float | None:
    """How much of what the day's sun could cover the house made its own, on
    days that are a real test: the night before ran the battery down, so
    nothing was carried over, and the sun could not cover the whole day's use.
    None on other days, which neither count nor set the bar."""
    used, cover = f.sun_used, f.sun_cover
    night_before = c.earlier(f.day, 1)
    if used is None or cover is None or night_before is None:
        return None
    if night_before.night_lasted is not False or not 0.3 <= cover <= 1.0:
        return None
    return used


def _night_owl(f: DayFacts, c: Context) -> bool | None:
    dull = c.quantile(lambda d: d.solar_kwh, 0.1, key="solar")
    if f.solar_kwh is None or f.night_lasted is None or dull is None:
        return None
    return f.solar_kwh <= dull and f.night_lasted


def _photo_finish(f: DayFacts, c: Context) -> int | None:
    if f.night_lasted is None or f.night_spare_kwh is None:
        return None
    if not f.night_lasted or not c.profile.battery_kwh:
        return 0
    spare = f.night_spare_kwh / c.profile.battery_kwh
    return steps_met(spare, (0.01, 0.005, 0.002), lowest=True)


def _equinox(f: DayFacts, _c: Context) -> bool | None:
    if not is_equinox(f.day):
        return False
    ss = f.self_sufficiency
    return None if ss is None else abs(ss - 0.5) <= 0.02


def _groundhog(f: DayFacts, c: Context) -> bool | None:
    """A good day that made what the same day last year made, within 1 %."""
    year_ago = c.earlier(f.day, 365)
    median = c.quantile(lambda d: d.solar_kwh, 0.5, key="solar")
    if (
        f.solar_kwh is None
        or year_ago is None
        or year_ago.solar_kwh is None
        or median is None
    ):
        return None
    return (
        f.solar_kwh >= median
        and abs(f.solar_kwh - year_ago.solar_kwh) <= 0.01 * year_ago.solar_kwh
    )


# Counts over the whole history.


def _runs(holds: Holds, length: int) -> Count:
    """Count separate runs of *length* days in a row."""

    def count(days: Sequence[DayFacts], _c: Context) -> float:
        runs = streak = 0
        for f in days:
            if holds(f):
                streak += 1
                if streak == length:
                    runs, streak = runs + 1, 0
            elif holds(f) is not None:
                streak = 0
        return runs

    return count


def _first_grid_free_spring(days: Sequence[DayFacts], c: Context) -> float:
    years = set()
    for f in days:
        if c.season(f.day) == Season.SPRING and f.grid_free:
            years.add(f.day.year)
    return len(years)


def _thrifty_weeks(days: Sequence[DayFacts], c: Context) -> float:
    """Separate weeks whose house use was in your bottom 10 % for the season."""
    weeks, k = 0, 0
    past: list[tuple[Season, float]] = []
    while k + 7 <= len(days):
        week = days[k : k + 7]
        total = sum(f.house_kwh for f in week)
        season = c.season(week[-1].day)
        same = [t for s, t in past if s == season]
        if len(same) >= 8 and total <= np.quantile(same, 0.1):
            weeks += 1
            past.append((season, total))
            k += 7
            continue
        past.append((season, total))
        k += 1
    return weeks


def _wheel_of_the_year(days: Sequence[DayFacts], _c: Context) -> float:
    """The most calendar months with a grid-free night, in any twelve months
    in a row: the dark months are the hard ones."""
    months = sorted({f.day.year * 12 + f.day.month - 1 for f in days if f.night_lasted})
    best = 0
    for k, first in enumerate(months):
        best = max(best, sum(1 for m in months[k:] if m < first + 12))
    return best


def _mwh_per_10kwp(days: Sequence[DayFacts], c: Context) -> float:
    total = sum(f.solar_kwh or 0 for f in days)
    if not c.profile.kwp:
        return 0
    # Per 10 kWp of an ideal roof: an east–west roof's year counts for more.
    return total / 1000 / (c.profile.kwp / 10 * c.profile.relative_yield)


CATALOGUE: tuple[Badge, ...] = (
    Badge(
        "perfect_day",
        "Perfect day",
        "mdi:white-balance-sunny",
        "Grid-free, battery full and the morning forecast beaten",
        (Cap.BATTERY, Cap.GRID, Cap.SOLAR, Cap.FORECAST),
        (1, 5, 20),
        grade=flat(_perfect),
    ),
    Badge(
        "perfect_streak",
        "Perfect streak",
        "mdi:white-balance-sunny",
        "Perfect days in a row",
        (Cap.BATTERY, Cap.GRID, Cap.SOLAR, Cap.FORECAST),
        (1, 1, 1),
        steps=("2 in a row", "3 in a row", "5 in a row"),
        grade=run(_perfect, (2, 3, 5)),
        follows="perfect_day",
    ),
    Badge(
        "every_drop",
        "Every drop",
        "mdi:water-check",
        "The sun kept at home on a better-than-usual day",
        (Cap.SOLAR, Cap.GRID),
        (1, 5, 15),
        steps=("95 %", "97 %", "99 %"),
        grade=_every_drop,
    ),
    Badge(
        "power_plant",
        "Power plant",
        "mdi:factory",
        "Exported in a day, per kWp of panels",
        (Cap.SOLAR, Cap.GRID, Cap.ARRAY),
        (1, 10, 30),
        steps=("2 kWh", "2.5 kWh", "3 kWh"),
        grade=_power_plant,
    ),
    Badge(
        "off_grid_week",
        "Off-grid week",
        "mdi:calendar-check",
        "7 days in a row at least 95 % self-sufficient",
        (Cap.GRID,),
        (1, 3, 6),
        count=_runs(_self_sufficient(0.95), 7),
        unit="week",
    ),
    Badge(
        "silver_lining",
        "Silver lining",
        "mdi:weather-cloudy-arrow-right",
        "Beat one of the season's gloomiest forecasts by far",
        (Cap.SOLAR, Cap.FORECAST),
        (1, 5, 15),
        steps=("1.5 times", "twice", "2.5 times"),
        grade=_silver_lining,
    ),
    Badge(
        "bluebird_day",
        "Bluebird day",
        "mdi:weather-sunny",
        "The panels close to what a clear sky gives, among your days",
        (Cap.SOLAR, Cap.SKY),
        (1, 5, 10),
        steps=("top 20 %", "top 10 %", "top 3 %"),
        grade=top(lambda f, _c: f.sky_share, (0.2, 0.1, 0.03)),
    ),
    Badge(
        "sun_sponge",
        "Sun sponge",
        "mdi:sprout-outline",
        "From an empty battery, more of the sun made your own than on most such days",
        (Cap.SOLAR, Cap.GRID),
        (1, 10, 25),
        steps=("top 25 %", "top 10 %", "top 3 %"),
        grade=top(_sponge_test, (0.25, 0.1, 0.03)),
    ),
    Badge(
        "harvest_night",
        "Harvest night",
        "mdi:weather-night",
        "A grid-free autumn night",
        (Cap.BATTERY, Cap.SEASONS),
        (1, 10, 30),
        steps=("until sunrise", "10 % to spare", "25 % to spare"),
        season="autumn",
        grade=seasonal(Season.AUTUMN, _night_with_spare),
    ),
    Badge(
        "harvest_moon",
        "Harvest moon",
        "mdi:moon-full",
        "Grid-free autumn nights in a row",
        (Cap.BATTERY, Cap.SEASONS),
        (1, 1, 1),
        steps=("7 nights", "10 nights", "14 nights"),
        season="autumn",
        grade=seasonal(Season.AUTUMN, run(_autumn_night, (7, 10, 14))),
        follows="harvest_night",
    ),
    Badge(
        "last_light",
        "Last light",
        "mdi:leaf",
        "Battery full in the six weeks before midwinter",
        (Cap.BATTERY, Cap.SEASONS),
        (1, 10, 25),
        season="late autumn",
        grade=in_window(_late_autumn, flat(lambda f, _c: f.full)),
    ),
    Badge(
        "low_sun_hero",
        "Low-sun hero",
        "mdi:snowflake",
        "A winter day among your best for the sky it had",
        (Cap.SOLAR, Cap.SEASONS),
        (1, 3, 8),
        steps=("top 10 %", "top 5 %", "top 2 %"),
        season="winter",
        crowns=False,
        grade=seasonal(
            Season.WINTER, top(_sky_or_yield, (0.1, 0.05, 0.02), Season.WINTER)
        ),
    ),
    Badge(
        "longest_night",
        "Longest night",
        "mdi:moon-waning-crescent",
        "A grid-free night within ten days of midwinter",
        (Cap.BATTERY, Cap.SEASONS),
        (1, 3, 7),
        season="midwinter",
        grade=in_window(_midwinter, flat(lambda f, _c: f.night_lasted)),
    ),
    Badge(
        "winter_warrior",
        "Winter warrior",
        "mdi:shield-sun",
        "Self-sufficient on a winter day",
        (Cap.GRID, Cap.SEASONS),
        (1, 10, 30),
        steps=("80 %", "85 %", "90 %"),
        season="winter",
        grade=seasonal(
            Season.WINTER, at_least(lambda f: f.self_sufficiency, (0.8, 0.85, 0.9))
        ),
    ),
    Badge(
        "winter_legend",
        "Winter legend",
        "mdi:shield-crown",
        "Self-sufficient through a whole winter week",
        (Cap.GRID, Cap.SEASONS),
        (1, 1, 1),
        steps=("80 %", "85 %", "90 %"),
        season="winter",
        grade=_winter_week,
        follows="winter_warrior",
    ),
    Badge(
        "spring_awakening",
        "Spring awakening",
        "mdi:sprout",
        "A grid-free spring day, once a year",
        (Cap.GRID, Cap.SEASONS),
        (1, 3, 5),
        season="spring",
        count=_first_grid_free_spring,
        unit="year",
    ),
    Badge(
        "early_bird",
        "Early bird",
        "mdi:bird",
        "Battery full in spring earlier than most of your days",
        (Cap.BATTERY, Cap.SEASONS),
        (1, 10, 30),
        steps=("top 20 %", "top 10 %", "top 5 %"),
        season="spring",
        crowns=False,
        grade=seasonal(Season.SPRING, _early_bird),
    ),
    Badge(
        "midsummer",
        "Midsummer",
        "mdi:sun-wireless",
        "One of your best days, within ten days of midsummer",
        (Cap.SOLAR, Cap.SEASONS),
        (1, 3, 7),
        steps=("top 5 %", "top 3 %", "top 1 %"),
        season="midsummer",
        crowns=False,
        grade=in_window(
            _midsummer, top(lambda f, c: c.specific_yield(f), (0.05, 0.03, 0.01))
        ),
    ),
    Badge(
        "island_mode",
        "Island mode",
        "mdi:island",
        "Off the grid in summer, without a break",
        (Cap.GRID, Cap.SEASONS),
        (1, 5, 15),
        steps=("24 h", "48 h", "72 h"),
        season="summer",
        grade=seasonal(Season.SUMMER, _island),
    ),
    Badge(
        "wheel_of_the_year",
        "Wheel of the year",
        "mdi:calendar-sync",
        "Months with a grid-free night, in twelve months in a row",
        (Cap.BATTERY, Cap.SEASONS),
        (9, 11, 12),
        count=_wheel_of_the_year,
        unit="month",
    ),
    Badge(
        "steady_hand",
        "Steady hand",
        "mdi:hand-okay",
        "A borderline night that lasted",
        (Cap.ADVICE, Cap.BATTERY),
        (1, 5, 15),
        grade=flat(
            lambda f, _c: (
                None
                if f.said_outlook is None or f.night_lasted is None
                else f.said_outlook == "borderline" and f.night_lasted
            )
        ),
    ),
    Badge(
        "forecast_whisperer",
        "Forecast whisperer",
        "mdi:crystal-ball",
        'The morning\'s "full at" close to the real time',
        (Cap.ADVICE, Cap.BATTERY),
        (1, 10, 30),
        steps=("15 min", "10 min", "5 min"),
        grade=_whisperer,
    ),
    Badge(
        "oracle",
        "Oracle",
        "mdi:eye-outline",
        'The morning\'s "full at" within 15 minutes, days in a row',
        (Cap.ADVICE, Cap.BATTERY),
        (1, 1, 1),
        steps=("3 in a row", "5 in a row", "7 in a row"),
        grade=run(_whisper, (3, 5, 7)),
        follows="forecast_whisperer",
    ),
    Badge(
        "thrifty_week",
        "Thrifty week",
        "mdi:piggy-bank-outline",
        "A week's use in your bottom 10 % for the season",
        (Cap.HOUSE,),
        (1, 3, 6),
        count=_thrifty_weeks,
        unit="week",
    ),
    Badge(
        "night_owl",
        "Night owl",
        "mdi:owl",
        "A grid-free night after one of your dullest days",
        (Cap.BATTERY, Cap.SOLAR),
        (1, 1, 1),
        hidden=True,
        riddle="When the sun barely showed, the night still held",
        grade=flat(_night_owl),
    ),
    Badge(
        "photo_finish",
        "Photo finish",
        "mdi:flag-checkered",
        "A night that lasted with almost nothing to spare",
        (Cap.BATTERY,),
        (1, 1, 1),
        steps=("under 1 %", "under 0.5 %", "under 0.2 %"),
        hidden=True,
        riddle="A night won by a whisker",
        grade=_photo_finish,
    ),
    Badge(
        "equinox",
        "Equinox",
        "mdi:scale-balance",
        "Half the day's use from the sun, within 2 points, on an equinox",
        (Cap.GRID,),
        (1, 1, 1),
        hidden=True,
        riddle="Balance, on the day of balance",
        grade=flat(_equinox),
    ),
    Badge(
        "groundhog_day",
        "Groundhog day",
        "mdi:calendar-refresh",
        "A good day that made what the same day last year made, within 1 %",
        (Cap.SOLAR,),
        (1, 1, 1),
        hidden=True,
        riddle="Haven't we been here before?",
        grade=flat(_groundhog),
    ),
    Badge(
        "mwh_club",
        "MWh club",
        "mdi:trophy",
        "Lifetime yield, per 10 kWp of panels",
        (Cap.SOLAR, Cap.ARRAY),
        (1, 5, 10),
        count=_mwh_per_10kwp,
        unit="MWh",
    ),
)
