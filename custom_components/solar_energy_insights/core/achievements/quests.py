"""Quests: one for the next day each evening, one for the week on Mondays.

Targets come from your own days with a similar forecast, set so that about
three in five such days would have met them: a quest should be a stretch, not a
lottery and not a given.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta, tzinfo
from typing import Any

import numpy as np

from .facts import DayFacts
from .profile import Capability as Cap
from .profile import Profile, season_of

# The share of similar days that should meet a quest.
AIM = 0.6
# Forecasts within this share of the next day's count as similar.
SIMILAR = 0.25
MIN_SIMILAR = 8
# A quest met on more than this share of similar days is set aside.
TOO_EASY = 0.8


@dataclass(frozen=True, slots=True)
class Quest:
    id: str
    day: date
    kind: str
    title: str
    target: float
    reward: int
    # The last day it covers (a week's quest runs Monday to Sunday).
    until: date
    result: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["day"], out["until"] = self.day.isoformat(), self.until.isoformat()
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Quest:
        return cls(
            **{
                **data,
                "day": date.fromisoformat(data["day"]),
                "until": date.fromisoformat(data["until"]),
            }
        )


def _similar(
    facts: Sequence[DayFacts], profile: Profile, day: date, forecast: float | None
) -> list[DayFacts]:
    if forecast:
        near = [
            f
            for f in facts
            if f.forecast_kwh and abs(f.forecast_kwh - forecast) <= SIMILAR * forecast
        ]
        if len(near) >= MIN_SIMILAR:
            return near
    season = season_of(day, profile.southern)
    return [f for f in facts if season_of(f.day, profile.southern) == season] or list(
        facts
    )


def _reward(share_met: float) -> int:
    """25 XP for an easy one, up to 100 for a hard one."""
    return round(25 + 75 * (1 - share_met))


def _hm(minutes: float) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def next_day(
    facts: Sequence[DayFacts],
    profile: Profile,
    tz: tzinfo,
    day: date,
    forecast_kwh: float | None,
    outlook_tonight: str | None = None,
) -> Quest | None:
    """The quest for *day*, made the evening before. A borderline night ahead
    comes first: getting through it is the quest that matters."""
    if outlook_tonight == "borderline" and profile.has(Cap.BATTERY):
        tonight = day - timedelta(days=1)
        return Quest(
            f"night-{tonight}",
            tonight,
            "grid_free_night",
            "Get to sunrise without the grid",
            1,
            75,
            tonight,
        )
    similar = _similar(facts, profile, day, forecast_kwh)
    # Each option with the share of similar days that met it.
    options: list[tuple[Quest, float]] = []
    if profile.has(Cap.BATTERY):
        times = [m for f in similar if (m := f.full_minutes(tz)) is not None]
        if len(times) >= MIN_SIMILAR:
            fill_by = float(15 * round(float(np.quantile(times, AIM)) / 15))
            met = sum(1 for m in times if m <= fill_by) / len(similar)
            title = f"Fill the battery before {_hm(fill_by)}"
            quest = Quest(
                f"fill-{day}", day, "fill_before", title, fill_by, _reward(met), day
            )
            options.append((quest, met))
    if profile.has(Cap.SOLAR, Cap.GRID):
        kept = [k for f in similar if (k := f.kept_at_home) is not None]
        if len(kept) >= MIN_SIMILAR:
            target = float(np.round(np.quantile(kept, 1 - AIM) * 20) / 20)
            met = sum(1 for k in kept if k >= target) / len(kept)
            title = f"Keep {round(target * 100)} % of the sun at home"
            quest = Quest(
                f"keep-{day}", day, "keep_home", title, target, _reward(met), day
            )
            options.append((quest, met))
    if profile.has(Cap.GRID):
        ss = [v for f in similar if (v := f.self_sufficiency) is not None]
        if len(ss) >= MIN_SIMILAR:
            target = float(np.round(np.quantile(ss, 1 - AIM) * 20) / 20)
            if target > 0:
                met = sum(1 for v in ss if v >= target) / len(ss)
                title = f"Be {round(target * 100)} % self-sufficient"
                quest = Quest(
                    f"self-{day}",
                    day,
                    "self_sufficient",
                    title,
                    target,
                    _reward(met),
                    day,
                )
                options.append((quest, met))
    if not options:
        return None
    # A quest most similar days meet anyway is no quest: a measure at its ceiling
    # (100 % self-sufficient on most sunny days) makes the target the ceiling.
    stretching = [q for q, met in options if met <= TOO_EASY]
    if not stretching:
        return min(options, key=lambda o: o[1])[0]
    # A different kind each day, the same for the same day.
    return stretching[day.toordinal() % len(stretching)]


def this_week(
    facts: Sequence[DayFacts], profile: Profile, monday: date
) -> Quest | None:
    """Monday's quest: a number of grid-free nights this week, from the last four."""
    if not profile.has(Cap.BATTERY):
        return None
    recent = [f.night_lasted for f in facts if f.night_lasted is not None][-28:]
    if len(recent) < 14:
        return None
    nights = max(1, int(np.floor(sum(recent) / len(recent) * 7)))
    return Quest(
        f"week-{monday}",
        monday,
        "grid_free_nights",
        f"{nights} grid-free nights this week",
        nights,
        50 + 10 * nights,
        monday + timedelta(days=6),
    )


def judge(quest: Quest, facts: Sequence[DayFacts], tz: tzinfo) -> bool | None:
    """Met, missed, or not known yet."""
    by_day = {f.day: f for f in facts}
    if quest.kind == "grid_free_nights":
        days = [by_day.get(quest.day + timedelta(days=k)) for k in range(7)]
        lasted = [
            f.night_lasted for f in days if f is not None and f.night_lasted is not None
        ]
        if sum(lasted) >= quest.target:
            return True
        return None if len(lasted) < 7 else False
    f = by_day.get(quest.day)
    if f is None:
        return None
    if quest.kind == "grid_free_night":
        return f.night_lasted
    if quest.kind == "fill_before":
        if f.full is None:
            return None
        minutes = f.full_minutes(tz)
        return minutes is not None and minutes <= quest.target
    if quest.kind == "keep_home":
        return None if f.kept_at_home is None else f.kept_at_home >= quest.target
    if quest.kind == "self_sufficient":
        return (
            None if f.self_sufficiency is None else f.self_sufficiency >= quest.target
        )
    return None
