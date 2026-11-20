"""The whole trophy room, from the closed days' facts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, tzinfo
from typing import Any

from .badges import CROWN_XP, TIER_XP, TIERS, BadgeState, evaluate
from .editions import Edition, in_season, window
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
from .quests import AIMS, Quest, aim, quest_level


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
    quest_level: int


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
    badges = evaluate(facts, profile, tz, today)
    recs = records(facts, tz, profile.southern) if facts else []
    xp = (
        sum(scores.values())
        + sum(sum(TIER_XP[: b.tier]) + CROWN_XP * b.crowns for b in badges)
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
        quest_level=quest_level(quests),
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
            "solar_kwh": None if live is None else live.solar_kwh,
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
        "badges": [_badge(b, today, southern) for b in t.badges],
        "quests": [q.as_dict() for q in t.quests[-10:]],
        "quest_level": {
            "level": t.quest_level,
            "max": len(AIMS),
            "aim": aim(t.quest_level),
        },
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


def _badge(b: BadgeState, today: date, southern: bool) -> dict[str, Any]:
    """A badge as the view shows it. A hidden one keeps its name until earned;
    a locked one says only what it follows."""
    badge, known = b.badge, bool(b.count or b.tier or not b.badge.hidden)
    found = window(badge.season, today, southern) if badge.season else None
    return {
        "id": badge.id,
        "name": badge.name if known else None,
        "icon": badge.icon if known else "mdi:help",
        "description": badge.description if known else None,
        "riddle": badge.riddle if badge.hidden and not known else None,
        "hidden": badge.hidden,
        "season": badge.season,
        "in_season": in_season(badge.season, today, southern),
        "season_from": found[0].isoformat() if found else None,
        "season_until": found[1].isoformat() if found else None,
        "complete": b.tier >= len(badge.tiers),
        "next_tier_at": b.next_at,
        "count": b.count,
        "tier": b.tier,
        "tiers": list(badge.tiers),
        "next_at": b.next_at,
        "rarity": b.rarity,
        "rarity_name": b.rarity_name,
        "last_earned": b.earned_on[-1].isoformat() if b.earned_on else None,
        "steps": _steps(b),
        "follows": badge.follows,
        "locked": b.locked,
        "edition": _edition(b, today),
        "crowns": b.crowns,
    }


def _steps(b: BadgeState) -> list[dict[str, Any]] | None:
    """Each tier's step of the ladder: what a day must reach, how many such
    days the tier needs, how many there have been, and how rare they are."""
    if b.badge.steps is None or b.step_days is None:
        return None
    return [
        {
            "tier": TIERS[k],
            "label": label,
            "need": b.badge.tiers[k],
            "days": b.step_days[k],
            "share": round(b.step_days[k] / b.applied, 4) if b.applied else None,
        }
        for k, label in enumerate(b.badge.steps)
    ]


def _edition(b: BadgeState, today: date) -> dict[str, Any] | None:
    """This window's edition against the one before it and the best one: the
    bar this season is played to. Between windows, the last one played."""
    if not b.editions:
        return None
    now = b.editions[-1]
    played = [e for e in b.editions[:-1] if not e.partial]
    before = b.editions[-2] if len(b.editions) > 1 else None
    best = max(played, key=lambda e: e.days, default=None)

    def brief(e: Edition | None) -> dict[str, Any] | None:
        if e is None:
            return None
        return {
            "label": e.label,
            "days": e.days,
            "top": e.top,
            "partial": e.partial,
        }

    return {
        **(brief(now) or {}),
        "from": now.start.isoformat(),
        "until": now.end.isoformat(),
        "active": now.start <= today <= now.end,
        "crowned_on": now.crowned_on.isoformat() if now.crowned_on else None,
        # The bar to beat: the edition before, if it was played in full.
        "beat": None if before is None or before.partial else before.days + 1,
        "previous": brief(before),
        "best": brief(best),
    }
