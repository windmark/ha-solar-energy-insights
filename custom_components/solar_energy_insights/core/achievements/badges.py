"""The badge catalogue: rules over day facts, relative to the home.

Each rule says what it needs, in which season it counts, and how many earning
days make each tier. Thresholds that depend on the home (a good day, an early
full battery) are percentiles of the days *before* the one judged, so the whole
history replays the same way and nothing peeks ahead.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, tzinfo

import numpy as np

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
    season_of,
)

TIERS = ("bronze", "silver", "gold")
# XP for reaching each tier.
TIER_XP = (50, 100, 200)
# Days of history before a percentile threshold is trusted.
MIN_PAST = 14
RARE, LEGENDARY = 0.10, 0.02


class Context:
    """What a rule may know on a day: the home, and the days before it."""

    def __init__(self, profile: Profile, tz: tzinfo, past: Sequence[DayFacts]) -> None:
        self.profile = profile
        self.tz = tz
        self.past = past

    def season(self, day: date) -> Season:
        return season_of(day, self.profile.southern)

    def quantile(
        self,
        value: Callable[[DayFacts], float | None],
        share: float,
        season: Season | None = None,
    ) -> float | None:
        values = [
            v
            for f in self.past
            if (season is None or self.season(f.day) == season)
            and (v := value(f)) is not None
        ]
        if len(values) < MIN_PAST:
            return None
        return float(np.quantile(values, share))

    def specific_yield(self, f: DayFacts) -> float | None:
        """Yield per kWp; without a known size, the yield itself, which ranks
        days the same way against the home's own history."""
        if f.solar_kwh is None:
            return None
        return f.solar_kwh / self.profile.kwp if self.profile.kwp else f.solar_kwh


@dataclass(frozen=True, slots=True)
class Badge:
    id: str
    name: str
    icon: str
    description: str
    needs: tuple[Cap, ...]
    tiers: tuple[int, int, int]
    # A season, or a window inside one; None counts all year.
    season: str | None = None
    hidden: bool = False
    riddle: str = ""
    # A day earns it; or, for runs and milestones, a whole history counts.
    earns: Callable[[DayFacts, Context], bool | None] | None = None
    count: Callable[[Sequence[DayFacts], Context], float] | None = None


def _self_sufficient(share: float) -> Callable[[DayFacts], bool | None]:
    def holds(f: DayFacts) -> bool | None:
        ss = f.self_sufficiency
        return None if ss is None else ss >= share

    return holds


def _perfect(f: DayFacts, _c: Context) -> bool | None:
    if f.grid_free is None or f.full is None or f.beat_forecast is None:
        return None
    return f.grid_free and f.full and f.beat_forecast


def _every_drop(f: DayFacts, c: Context) -> bool | None:
    kept, sy = f.kept_at_home, c.specific_yield(f)
    median = c.quantile(c.specific_yield, 0.5)
    if kept is None or sy is None or median is None:
        return None
    return kept >= 0.95 and sy >= median


def _power_plant(f: DayFacts, c: Context) -> bool | None:
    if f.export_kwh is None or not c.profile.kwp:
        return None
    # 2 kWh per kWp on a roof facing the equator; less on one that faces away.
    return f.export_kwh / c.profile.kwp >= 2.0 * c.profile.relative_yield


def _silver_lining(f: DayFacts, c: Context) -> bool | None:
    if f.solar_kwh is None or not f.forecast_kwh:
        return None
    gloomy = c.quantile(lambda d: d.forecast_kwh, 0.2, c.season(f.day))
    if gloomy is None:
        return None
    return f.forecast_kwh <= gloomy and f.solar_kwh >= 1.5 * f.forecast_kwh


type Earns = Callable[[DayFacts, "Context"], bool | None]
type Counts = Callable[[Sequence[DayFacts], "Context"], float]


def _in(season: Season, rule: Callable[[DayFacts], bool | None]) -> Earns:
    def earns(f: DayFacts, c: Context) -> bool | None:
        if c.season(f.day) != season:
            return False
        return rule(f)

    return earns


def _late_autumn_full(f: DayFacts, c: Context) -> bool | None:
    if days_before_winter_solstice(f.day, c.profile.southern) > 42:
        return False
    return f.full


def _low_sun_hero(f: DayFacts, c: Context) -> bool | None:
    if c.season(f.day) != Season.WINTER:
        return False
    sy = c.specific_yield(f)
    top = c.quantile(c.specific_yield, 0.9, Season.WINTER)
    return None if sy is None or top is None else sy >= top


def _longest_night(f: DayFacts, c: Context) -> bool | None:
    if days_from_winter_solstice(f.day, c.profile.southern) > 10:
        return False
    return f.night_lasted


def _early_bird(f: DayFacts, c: Context) -> bool | None:
    if c.season(f.day) != Season.SPRING:
        return False
    minutes = f.full_minutes(c.tz)
    early = c.quantile(lambda d: d.full_minutes(c.tz), 0.2)
    if f.full is None or early is None:
        return None
    return minutes is not None and minutes <= early


def _midsummer(f: DayFacts, c: Context) -> bool | None:
    if days_from_summer_solstice(f.day, c.profile.southern) > 10:
        return False
    sy = c.specific_yield(f)
    top = c.quantile(c.specific_yield, 0.95)
    return None if sy is None or top is None else sy >= top


def _steady_hand(f: DayFacts, _c: Context) -> bool | None:
    if f.said_outlook is None or f.night_lasted is None:
        return None
    return f.said_outlook == "borderline" and f.night_lasted


def _whisperer(f: DayFacts, _c: Context) -> bool | None:
    if f.said_full is None or f.full is None:
        return None
    if f.said_full_at is None or f.full_at is None:
        return False
    return abs((f.said_full_at - f.full_at).total_seconds()) <= 15 * 60


def _night_owl(f: DayFacts, c: Context) -> bool | None:
    dull = c.quantile(lambda d: d.solar_kwh, 0.1)
    if f.solar_kwh is None or f.night_lasted is None or dull is None:
        return None
    return f.solar_kwh <= dull and f.night_lasted


def _photo_finish(f: DayFacts, c: Context) -> bool | None:
    if f.night_lasted is None or f.night_spare_kwh is None:
        return None
    return f.night_lasted and f.night_spare_kwh < 0.01 * c.profile.battery_kwh


def _equinox(f: DayFacts, _c: Context) -> bool | None:
    if not is_equinox(f.day):
        return False
    ss = f.self_sufficiency
    return None if ss is None else abs(ss - 0.5) <= 0.02


def _runs(holds: Callable[[DayFacts], bool | None], length: int) -> Counts:
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


def _mwh_per_10kwp(days: Sequence[DayFacts], c: Context) -> float:
    total = sum(f.solar_kwh or 0 for f in days)
    if not c.profile.kwp:
        return 0
    # Per 10 kWp of an ideal roof: an east–west roof's year counts for more.
    return total / 1000 / (c.profile.kwp / 10 * c.profile.relative_yield)


def _cycles(days: Sequence[DayFacts], _c: Context) -> float:
    """The battery's own count of full cycles, as last reported."""
    counts = [f.battery_cycles for f in days if f.battery_cycles is not None]
    return max(counts) if counts else 0.0


CATALOGUE: tuple[Badge, ...] = (
    Badge(
        "perfect_day",
        "Perfect day",
        "mdi:white-balance-sunny",
        "Grid-free, battery full and the morning forecast beaten",
        (Cap.BATTERY, Cap.GRID, Cap.SOLAR, Cap.FORECAST),
        (1, 5, 20),
        earns=_perfect,
    ),
    Badge(
        "every_drop",
        "Every drop",
        "mdi:water-check",
        "95 % of the sun kept at home on a better-than-usual day",
        (Cap.SOLAR, Cap.GRID),
        (1, 5, 15),
        earns=_every_drop,
    ),
    Badge(
        "power_plant",
        "Power plant",
        "mdi:factory",
        "2 kWh exported per kWp in a day",
        (Cap.SOLAR, Cap.GRID, Cap.ARRAY),
        (1, 10, 30),
        earns=_power_plant,
    ),
    Badge(
        "off_grid_week",
        "Off-grid week",
        "mdi:calendar-check",
        "7 days in a row at least 95 % self-sufficient",
        (Cap.GRID,),
        (1, 3, 6),
        count=_runs(_self_sufficient(0.95), 7),
    ),
    Badge(
        "silver_lining",
        "Silver lining",
        "mdi:weather-cloudy-arrow-right",
        "1.5 times one of the season's gloomiest forecasts",
        (Cap.SOLAR, Cap.FORECAST),
        (1, 5, 15),
        earns=_silver_lining,
    ),
    Badge(
        "harvest_night",
        "Harvest night",
        "mdi:weather-night",
        "A grid-free autumn night",
        (Cap.BATTERY, Cap.SEASONS),
        (1, 10, 30),
        season="autumn",
        earns=_in(Season.AUTUMN, lambda f: f.night_lasted),
    ),
    Badge(
        "last_light",
        "Last light",
        "mdi:leaf",
        "Battery full in the six weeks before midwinter",
        (Cap.BATTERY, Cap.SEASONS),
        (1, 10, 25),
        season="late autumn",
        earns=_late_autumn_full,
    ),
    Badge(
        "low_sun_hero",
        "Low-sun hero",
        "mdi:snowflake",
        "A winter day in your top 10 % for yield",
        (Cap.SOLAR, Cap.SEASONS),
        (1, 3, 8),
        season="winter",
        earns=_low_sun_hero,
    ),
    Badge(
        "longest_night",
        "Longest night",
        "mdi:moon-waning-crescent",
        "A grid-free night within ten days of midwinter",
        (Cap.BATTERY, Cap.SEASONS),
        (1, 3, 7),
        season="midwinter",
        earns=_longest_night,
    ),
    Badge(
        "winter_warrior",
        "Winter warrior",
        "mdi:shield-sun",
        "At least 80 % self-sufficient in winter",
        (Cap.GRID, Cap.SEASONS),
        (1, 10, 30),
        season="winter",
        earns=_in(Season.WINTER, _self_sufficient(0.8)),
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
    ),
    Badge(
        "early_bird",
        "Early bird",
        "mdi:bird",
        "Battery full in spring earlier than four in five of your days",
        (Cap.BATTERY, Cap.SEASONS),
        (1, 10, 30),
        season="spring",
        earns=_early_bird,
    ),
    Badge(
        "midsummer",
        "Midsummer",
        "mdi:sun-wireless",
        "A top 5 % day within ten days of midsummer",
        (Cap.SOLAR, Cap.SEASONS),
        (1, 3, 7),
        season="midsummer",
        earns=_midsummer,
    ),
    Badge(
        "island_mode",
        "Island mode",
        "mdi:island",
        "24 hours without the grid in summer",
        (Cap.GRID, Cap.SEASONS),
        (1, 5, 15),
        season="summer",
        earns=_in(
            Season.SUMMER,
            lambda f: None if f.offgrid_hours is None else f.offgrid_hours >= 24,
        ),
    ),
    Badge(
        "steady_hand",
        "Steady hand",
        "mdi:hand-okay",
        "A borderline night that lasted",
        (Cap.ADVICE, Cap.BATTERY),
        (1, 5, 15),
        earns=_steady_hand,
    ),
    Badge(
        "forecast_whisperer",
        "Forecast whisperer",
        "mdi:crystal-ball",
        'The morning\'s "full at" within 15 minutes of the real time',
        (Cap.ADVICE, Cap.BATTERY),
        (1, 10, 30),
        earns=_whisperer,
    ),
    Badge(
        "thrifty_week",
        "Thrifty week",
        "mdi:piggy-bank-outline",
        "A week's use in your bottom 10 % for the season",
        (Cap.HOUSE,),
        (1, 3, 6),
        count=_thrifty_weeks,
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
        earns=_night_owl,
    ),
    Badge(
        "photo_finish",
        "Photo finish",
        "mdi:flag-checkered",
        "A night that lasted with less than 1 % to spare",
        (Cap.BATTERY,),
        (1, 1, 1),
        hidden=True,
        riddle="A night won by a whisker",
        earns=_photo_finish,
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
        earns=_equinox,
    ),
    Badge(
        "mwh_club",
        "MWh club",
        "mdi:trophy",
        "Lifetime yield, per 10 kWp of panels",
        (Cap.SOLAR, Cap.ARRAY),
        (1, 5, 10),
        count=_mwh_per_10kwp,
    ),
    Badge(
        "cycle_master",
        "Cycle master",
        "mdi:battery-sync",
        "Full battery cycles, as the battery counts them",
        (Cap.CYCLES,),
        (100, 250, 500),
        count=_cycles,
    ),
)


@dataclass(frozen=True, slots=True)
class BadgeState:
    badge: Badge
    count: float
    tier: int
    next_at: int | None
    # Share of the days it applied to that earned it; None for runs and milestones.
    rarity: float | None
    earned_on: tuple[date, ...]
    # When each tier was reached.
    tier_days: tuple[date, ...]

    @property
    def rarity_name(self) -> str | None:
        if self.rarity is None or self.count == 0:
            return None
        if self.rarity < LEGENDARY:
            return "legendary"
        if self.rarity < RARE:
            return "rare"
        return "common"


def evaluate(
    facts: Sequence[DayFacts], profile: Profile, tz: tzinfo
) -> list[BadgeState]:
    """Every badge the home can earn, with its count, tier and rarity."""
    out = []
    for badge in CATALOGUE:
        if not profile.has(*badge.needs):
            continue
        earned: list[date] = []
        applied = 0
        tier_days: list[date] = []
        if badge.earns is not None:
            for k, f in enumerate(facts):
                verdict = badge.earns(f, Context(profile, tz, facts[:k]))
                if verdict is None:
                    continue
                applied += 1
                if verdict:
                    earned.append(f.day)
                    if (
                        len(tier_days) < 3
                        and len(earned) >= badge.tiers[len(tier_days)]
                    ):
                        tier_days.append(f.day)
            count: float = len(earned)
        else:
            assert badge.count is not None
            count = badge.count(facts, Context(profile, tz, facts))
            # When each tier was reached: counts only grow as days are added, so
            # the first day a prefix reaches a tier is found by halving.
            for need in badge.tiers:
                if count < need:
                    break
                lo, hi = 0, len(facts) - 1
                while lo < hi:
                    mid = (lo + hi) // 2
                    sofar = badge.count(
                        facts[: mid + 1], Context(profile, tz, facts[: mid + 1])
                    )
                    if sofar >= need:
                        hi = mid
                    else:
                        lo = mid + 1
                tier_days.append(facts[lo].day)
        tier = sum(1 for t in badge.tiers if count >= t)
        out.append(
            BadgeState(
                badge=badge,
                count=round(count, 2),
                tier=tier,
                next_at=badge.tiers[tier] if tier < 3 else None,
                rarity=len(earned) / applied
                if badge.earns is not None and applied
                else None,
                earned_on=tuple(earned),
                tier_days=tuple(tier_days),
            )
        )
    return out
