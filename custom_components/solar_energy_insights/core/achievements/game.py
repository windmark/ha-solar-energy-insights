"""The daily score, XP and levels, ranks, streaks and records.

All of it is a function of the closed days' facts: replaying the same history
gives the same game.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, tzinfo

import numpy as np

from .facts import DayFacts
from .profile import Season, season_of

# ── The daily score: four parts, weighted; parts a home cannot measure drop out.

WEIGHTS = {
    "self_sufficiency": 40,
    "kept_at_home": 30,
    "battery_used": 15,
    "performance": 15,
}


def score_parts(f: DayFacts, floor_pct: float = 5.0) -> dict[str, float]:
    """Each part from 0 to 1, for the parts this day can measure."""
    parts: dict[str, float] = {}
    if (s := f.self_sufficiency) is not None:
        parts["self_sufficiency"] = s
    if (k := f.kept_at_home) is not None:
        parts["kept_at_home"] = k
    if f.soc_max is not None and f.soc_min is not None:
        usable = max(100 - floor_pct, 1.0)
        parts["battery_used"] = max(0.0, min(1.0, (f.soc_max - f.soc_min) / usable))
    if f.solar_kwh is not None and f.forecast_kwh:
        parts["performance"] = (
            1.0 if f.forecast_kwh < 0.5 else min(1.0, f.solar_kwh / f.forecast_kwh)
        )
    return parts


def score(f: DayFacts, floor_pct: float = 5.0) -> int:
    parts = score_parts(f, floor_pct)
    if not parts:
        return 0
    total = sum(WEIGHTS[k] for k in parts)
    return round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()) / total)


# ── Levels: the same curve as before, with names.

LEVEL_NAMES = (
    "Spark",
    "Glow",
    "Sunbeam",
    "Photon",
    "Flare",
    "Corona",
    "Solstice",
    "Zenith",
)


def xp_for_level(level: int) -> int:
    """XP needed to reach level + 1."""
    return 50 * level * (level + 1)


@dataclass(frozen=True, slots=True)
class Level:
    level: int
    name: str
    xp: int
    floor_xp: int
    next_xp: int

    @property
    def progress(self) -> float:
        return (self.xp - self.floor_xp) / max(self.next_xp - self.floor_xp, 1)


def level_of(xp: int) -> Level:
    level = 1
    while xp >= xp_for_level(level):
        level += 1
    # Every three levels a new name; past the last, numbered: Zenith II, III…
    step = (level - 1) // 3
    if step < len(LEVEL_NAMES):
        name = LEVEL_NAMES[step]
    else:
        name = f"{LEVEL_NAMES[-1]} {_roman(step - len(LEVEL_NAMES) + 2)}"
    floor_xp = xp_for_level(level - 1) if level > 1 else 0
    return Level(level, name, xp, floor_xp, xp_for_level(level))


def _roman(n: int) -> str:
    out = ""
    for value, letters in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out, n = out + letters, n - value
    return out


# ── Ranks from the 30-day average, with cut-offs that follow the season.

RANKS = (("Bronze", 0), ("Silver", 45), ("Gold", 60), ("Platinum", 72), ("Diamond", 85))


@dataclass(frozen=True, slots=True)
class Rank:
    name: str
    average: float
    next_name: str | None
    next_at: float | None
    # How the cut-offs were scaled for this season (1 = as set).
    season_factor: float


def rank_of(scores: Sequence[tuple[date, int]], today: date, southern: bool) -> Rank:
    """From the last 30 days' average. In a season whose days score lower than
    your year as a whole, the cut-offs come down with it, so a dark December is
    not doomed to Bronze."""
    recent = [s for d, s in scores if 0 <= (today - d).days < 30]
    average = float(np.mean(recent)) if recent else 0.0
    season = season_of(today, southern)
    same = [s for d, s in scores if season_of(d, southern) == season]
    everything = [s for _, s in scores]
    factor = 1.0
    if len(same) >= 14 and len(everything) >= 60:
        ratio = float(np.median(same)) / max(float(np.median(everything)), 1.0)
        factor = min(max(ratio, 0.6), 1.0)
    cutoffs = [(name, at * factor) for name, at in RANKS]
    index = max(i for i, (_, at) in enumerate(cutoffs) if average >= at)
    nxt = cutoffs[index + 1] if index + 1 < len(cutoffs) else None
    return Rank(
        name=cutoffs[index][0],
        average=round(average, 1),
        next_name=nxt[0] if nxt else None,
        next_at=round(nxt[1], 1) if nxt else None,
        season_factor=round(factor, 2),
    )


# ── Streaks, with a weather shield.

SHIELD_EVERY = 7
MAX_SHIELDS = 2
# A morning forecast in the bottom share of its season's forecasts is weather.
WEATHER_SHARE = 0.2


@dataclass(frozen=True, slots=True)
class StreakRule:
    id: str
    name: str
    icon: str
    description: str
    holds: Callable[[DayFacts], bool | None]


STREAKS: tuple[StreakRule, ...] = (
    StreakRule(
        "self_sufficient",
        "Self-sufficient",
        "mdi:home-lightning-bolt",
        "at least 95 % of the day without the grid",
        lambda f: None if f.self_sufficiency is None else f.self_sufficiency >= 0.95,
    ),
    StreakRule(
        "full",
        "Battery filled",
        "mdi:battery-check",
        "reached its limit every day",
        lambda f: f.full,
    ),
    StreakRule(
        "beat_forecast",
        "Beat the forecast",
        "mdi:weather-sunny-alert",
        "more than the morning forecast",
        lambda f: f.beat_forecast,
    ),
    StreakRule(
        "grid_free_night",
        "Grid-free nights",
        "mdi:weather-night",
        "the battery lasted until sunrise",
        lambda f: f.night_lasted,
    ),
)


@dataclass(frozen=True, slots=True)
class Streak:
    id: str
    name: str
    icon: str
    description: str
    current: int
    best: int
    shields: int
    # True if the current run is the best so far (and longer than one day).
    personal_best: bool


def weather_days(facts: Sequence[DayFacts], southern: bool) -> set[date]:
    """Days whose morning forecast was among the gloomiest of their season, by
    the days before them."""
    out: set[date] = set()
    seen: dict[Season, list[float]] = {}
    for f in facts:
        season = season_of(f.day, southern)
        past = seen.setdefault(season, [])
        if f.forecast_kwh is not None:
            # Strictly below: on a run of equal forecasts, none is gloomy.
            if len(past) >= 10 and f.forecast_kwh < np.quantile(past, WEATHER_SHARE):
                out.add(f.day)
            past.append(f.forecast_kwh)
    return out


def streaks(facts: Sequence[DayFacts], southern: bool) -> list[Streak]:
    weather = weather_days(facts, southern)
    out = []
    for rule in STREAKS:
        current = best = shields = run_since_shield = 0
        for f in facts:
            holds = rule.holds(f)
            if holds is None:
                continue
            if holds:
                current += 1
                run_since_shield += 1
                if run_since_shield >= SHIELD_EVERY:
                    shields = min(shields + 1, MAX_SHIELDS)
                    run_since_shield = 0
            elif f.day in weather and shields > 0:
                # The weather broke it, and a shield takes the hit.
                shields -= 1
            else:
                current = run_since_shield = 0
            best = max(best, current)
        out.append(
            Streak(
                rule.id,
                rule.name,
                rule.icon,
                rule.description,
                current,
                best,
                shields,
                personal_best=current > 1 and current >= best,
            )
        )
    return out


# ── Records.


@dataclass(frozen=True, slots=True)
class RecordRule:
    id: str
    name: str
    icon: str
    unit: str
    value: Callable[[DayFacts, tzinfo], float | None]
    # Lower is better (earliest full, thriftiest day).
    lowest: bool = False


RECORDS: tuple[RecordRule, ...] = (
    RecordRule(
        "best_day",
        "Best day",
        "mdi:white-balance-sunny",
        "kWh",
        lambda f, _: f.solar_kwh,
    ),
    RecordRule("peak", "Peak power", "mdi:summit", "kW", lambda f, _: f.peak_kw),
    RecordRule(
        "export",
        "Most exported",
        "mdi:transmission-tower-export",
        "kWh",
        lambda f, _: f.export_kwh,
    ),
    RecordRule(
        "offgrid",
        "Longest off the grid",
        "mdi:island",
        "h",
        lambda f, _: f.offgrid_hours,
    ),
    RecordRule(
        "earliest_full",
        "Earliest full battery",
        "mdi:battery-clock",
        "min",
        lambda f, tz: f.full_minutes(tz),
        lowest=True,
    ),
    RecordRule(
        "thriftiest",
        "Thriftiest day",
        "mdi:leaf",
        "kWh",
        lambda f, _: f.house_kwh if f.house_kwh > 0.5 else None,
        lowest=True,
    ),
)


@dataclass(frozen=True, slots=True)
class Record:
    id: str
    name: str
    icon: str
    unit: str
    value: float
    day: date
    # The best this season, which keeps records reachable after the first summer.
    season_value: float | None
    season_day: date | None
    # How the most recent day ranked: the share of days it beat or matched.
    last_top_pct: float | None = None
    broken_on: list[date] = field(default_factory=list)


def records(facts: Sequence[DayFacts], tz: tzinfo, southern: bool) -> list[Record]:
    out = []
    today_season = season_of(facts[-1].day, southern) if facts else None
    for rule in RECORDS:
        rows = [(f.day, v) for f in facts if (v := rule.value(f, tz)) is not None]
        if not rows:
            continue
        lowest = rule.lowest

        def better(a: float, b: float, lowest: bool = lowest) -> bool:
            return a < b if lowest else a > b

        best_day, best = rows[0]
        broken = []
        for day, v in rows[1:]:
            if better(v, best):
                best_day, best = day, v
                broken.append(day)
        season_rows = [
            (d, v) for d, v in rows if season_of(d, southern) == today_season
        ]
        season = None
        for d, v in season_rows:
            if season is None or better(v, season[1]):
                season = (d, v)
        last = rows[-1][1]
        # The latest day's place: the share of days at least as good, so 2 means
        # "top 2 % of your days".
        as_good = sum(1 for _, v in rows if (v <= last if rule.lowest else v >= last))
        out.append(
            Record(
                rule.id,
                rule.name,
                rule.icon,
                rule.unit,
                best,
                best_day,
                season[1] if season else None,
                season[0] if season else None,
                last_top_pct=round(as_good / len(rows) * 100, 1),
                broken_on=broken,
            )
        )
    return out
