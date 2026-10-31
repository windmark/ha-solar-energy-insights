"""The whole trophy room, from the closed days' facts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, tzinfo
from typing import Any

from .badges import TIER_XP, BadgeState, evaluate
from .facts import DayFacts
from .game import (
    Level,
    Rank,
    Record,
    Streak,
    level_of,
    rank_of,
    records,
    score,
    score_parts,
    streaks,
)
from .profile import Profile, season_of
from .quests import Quest


@dataclass(frozen=True, slots=True)
class Month:
    month: str
    days: int
    score: float
    best_day: date | None
    best_day_kwh: float | None
    kept_kwh: float | None
    export_kwh: float | None
    self_sufficiency: float | None
    nights_lasted: int
    nights: int
    badges: tuple[str, ...]
    records: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Trophies:
    scores: tuple[tuple[date, int], ...]
    level: Level
    rank: Rank
    streaks: tuple[Streak, ...]
    records: tuple[Record, ...]
    badges: tuple[BadgeState, ...]
    quests: tuple[Quest, ...]
    months: tuple[Month, ...]
    profile: Profile


def _months(
    facts: Sequence[DayFacts],
    scores: dict[date, int],
    badges: Sequence[BadgeState],
    recs: Sequence[Record],
) -> list[Month]:
    out = []
    keys = sorted({f.day.strftime("%Y-%m") for f in facts})
    for key in keys:
        days = [f for f in facts if f.day.strftime("%Y-%m") == key]
        solar = [f for f in days if f.solar_kwh is not None]
        best = max(solar, key=lambda f: f.solar_kwh or 0) if solar else None
        kept = [
            (f.solar_kwh or 0) - (f.export_kwh or 0)
            for f in days
            if f.solar_kwh is not None and f.export_kwh is not None
        ]
        exported = [f.export_kwh for f in days if f.export_kwh is not None]
        ss = [s for f in days if (s := f.self_sufficiency) is not None]
        nights = [f.night_lasted for f in days if f.night_lasted is not None]
        out.append(
            Month(
                month=key,
                days=len(days),
                score=round(sum(scores[f.day] for f in days) / len(days), 1),
                best_day=best.day if best else None,
                best_day_kwh=best.solar_kwh if best else None,
                kept_kwh=round(sum(kept), 1) if kept else None,
                export_kwh=round(sum(exported), 1) if exported else None,
                self_sufficiency=round(sum(ss) / len(ss), 3) if ss else None,
                nights_lasted=sum(nights),
                nights=len(nights),
                badges=tuple(
                    b.badge.id
                    for b in badges
                    for d in b.tier_days
                    if d.strftime("%Y-%m") == key
                ),
                records=tuple(
                    r.id
                    for r in recs
                    for d in r.broken_on
                    if d.strftime("%Y-%m") == key
                ),
            )
        )
    return out


def compute(
    facts: Sequence[DayFacts],
    profile: Profile,
    tz: tzinfo,
    today: date,
    quests: Sequence[Quest] = (),
    floor_pct: float = 5.0,
) -> Trophies:
    facts = sorted(facts, key=lambda f: f.day)
    scores = {f.day: score(f, floor_pct) for f in facts}
    badges = evaluate(facts, profile, tz)
    recs = records(facts, tz, profile.southern) if facts else []
    xp = (
        sum(scores.values())
        + sum(sum(TIER_XP[: b.tier]) for b in badges)
        + sum(q.reward for q in quests if q.result)
    )
    return Trophies(
        scores=tuple(sorted(scores.items())),
        level=level_of(xp),
        rank=rank_of(list(scores.items()), today, profile.southern),
        streaks=tuple(streaks(facts, profile.southern)),
        records=tuple(recs),
        badges=tuple(badges),
        quests=tuple(quests),
        months=tuple(_months(facts, scores, badges, recs)),
        profile=profile,
    )


def as_dict(
    t: Trophies,
    today: date,
    live: DayFacts | None = None,
    floor_pct: float = 5.0,
    prices: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Everything the Achievements view shows, as JSON."""
    southern = t.profile.southern
    last = t.scores[-1] if t.scores else None
    live_parts = score_parts(live, floor_pct) if live else {}
    return {
        "today": {
            "score": score(live, floor_pct) if live else None,
            "parts": {k: round(v, 3) for k, v in live_parts.items()},
        },
        "last_day": {"day": last[0].isoformat(), "score": last[1]} if last else None,
        "level": {
            "level": t.level.level,
            "name": t.level.name,
            "xp": t.level.xp,
            "floor_xp": t.level.floor_xp,
            "next_xp": t.level.next_xp,
            "progress": round(t.level.progress, 3),
        },
        "rank": {
            "name": t.rank.name,
            "average": t.rank.average,
            "next_name": t.rank.next_name,
            "next_at": t.rank.next_at,
            "season_factor": t.rank.season_factor,
        },
        "streaks": [
            {
                "id": s.id,
                "name": s.name,
                "icon": s.icon,
                "description": s.description,
                "current": s.current,
                "best": s.best,
                "shields": s.shields,
                "personal_best": s.personal_best,
            }
            for s in t.streaks
        ],
        "records": [
            {
                "id": r.id,
                "name": r.name,
                "icon": r.icon,
                "unit": r.unit,
                "value": r.value,
                "day": r.day.isoformat(),
                "season_value": r.season_value,
                "season_day": r.season_day.isoformat() if r.season_day else None,
                "last_top_pct": r.last_top_pct,
            }
            for r in t.records
        ],
        "badges": [
            {
                "id": b.badge.id,
                "name": b.badge.name if (b.count or not b.badge.hidden) else None,
                "icon": b.badge.icon if (b.count or not b.badge.hidden) else "mdi:help",
                "description": b.badge.description
                if (b.count or not b.badge.hidden)
                else None,
                "riddle": b.badge.riddle if b.badge.hidden and not b.count else None,
                "hidden": b.badge.hidden,
                "season": b.badge.season,
                "in_season": _in_season(b.badge.season, today, southern),
                "count": b.count,
                "tier": b.tier,
                "tiers": list(b.badge.tiers),
                "next_at": b.next_at,
                "rarity": b.rarity,
                "rarity_name": b.rarity_name,
                "last_earned": b.earned_on[-1].isoformat() if b.earned_on else None,
            }
            for b in t.badges
        ],
        "quests": [q.as_dict() for q in t.quests[-10:]],
        "months": [_month(m, prices) for m in t.months[-13:]],
        "profile": {
            "capabilities": sorted(str(c) for c in t.profile.capabilities),
            "kwp": t.profile.kwp,
            "relative_yield": round(t.profile.relative_yield, 3),
            "battery_kwh": t.profile.battery_kwh,
            "hemisphere": "southern" if southern else "northern",
            "season": str(season_of(today, southern)),
        },
    }


def _in_season(season: str | None, today: date, southern: bool) -> bool:
    from .profile import (
        days_before_winter_solstice,
        days_from_summer_solstice,
        days_from_winter_solstice,
    )

    if season is None:
        return True
    if season == "late autumn":
        return days_before_winter_solstice(today, southern) <= 42
    if season == "midwinter":
        return days_from_winter_solstice(today, southern) <= 10
    if season == "midsummer":
        return days_from_summer_solstice(today, southern) <= 10
    return str(season_of(today, southern)) == season


def _month(m: Month, prices: tuple[float, float] | None) -> dict[str, Any]:
    """A month's recap; with prices, what the sun was worth."""
    out: dict[str, Any] = {
        "month": m.month,
        "days": m.days,
        "score": m.score,
        "best_day": m.best_day.isoformat() if m.best_day else None,
        "best_day_kwh": m.best_day_kwh,
        "kept_kwh": m.kept_kwh,
        "export_kwh": m.export_kwh,
        "self_sufficiency": m.self_sufficiency,
        "nights_lasted": m.nights_lasted,
        "nights": m.nights,
        "badges": list(m.badges),
        "records": list(m.records),
    }
    if prices is not None:
        price, feed_in = prices
        out["saved"] = None if m.kept_kwh is None else round(m.kept_kwh * price, 2)
        out["earned"] = (
            None if m.export_kwh is None else round(m.export_kwh * feed_in, 2)
        )
    return out
