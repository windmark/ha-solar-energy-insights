"""Moments: what was just achieved, each with a title, a message and a line
that explains it. Announced as events and kept for a while, so a dashboard can
show each one from when it happened until a day later."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .badges import BadgeState
    from .game import Level, Record
    from .quests import Quest
    from .trophies import Month

TIERS = ("bronze", "silver", "gold")
# Kept a little longer than a dashboard shows them, so a late look still finds them.
KEEP = timedelta(days=2)
MAX_KEPT = 30


def _day(d: date) -> str:
    return f"{d:%A} {d.day} {d:%b}"


def value_text(value: float, unit: str) -> str:
    """A record's value as people read it."""
    if unit == "min":
        minutes = round(value)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    if unit == "kW":
        return f"{value:.2f} kW"
    if unit == "h":
        return f"{value:.0f} h"
    if unit == "kWh":
        return f"{value:.1f} kWh"
    return f"{value:g} {unit}"


def level(lv: Level) -> dict[str, Any]:
    return {
        "kind": "level",
        "title": f"Level {lv.level}: {lv.name}",
        "message": f"Solar level {lv.level} reached: {lv.name}.",
        "explain": (
            "Each closed day adds its score out of 100 as XP; badge tiers and "
            f"quests add more. {max(lv.next_xp - lv.xp, 0)} XP to level "
            f"{lv.level + 1}."
        ),
        "icon": "mdi:star-shooting",
    }


def badge(b: BadgeState) -> dict[str, Any]:
    tier = TIERS[b.tier - 1]
    tiers = b.badge.tiers
    if b.tier < len(tiers):
        progress = f"{b.count:g} of {tiers[b.tier]:g} for {TIERS[b.tier]}."
    else:
        progress = "Its last tier: the badge is complete."
    rarity = ""
    name = b.rarity_name
    if b.rarity is not None and name is not None and name != "common":
        rarity = (
            f" {name.capitalize()}: {max(round(b.rarity * 100), 1)} % "
            "of your days earn it."
        )
    return {
        "kind": "badge",
        "badge": b.badge.id,
        "tier": tier,
        "title": f"{b.badge.name}: {tier}",
        "message": f"New badge, {b.badge.name} ({tier}): {b.badge.description}.",
        "explain": f"{b.badge.description}. {progress}{rarity}",
        "icon": b.badge.icon,
    }


def record(r: Record, since: date | None) -> dict[str, Any]:
    value = value_text(r.value, r.unit)
    best = "the best of all your days" + (
        f" since {since.day} {since:%b %Y}" if since else ""
    )
    return {
        "kind": "record",
        "record": r.id,
        "title": f"New record: {r.name}",
        "message": f"New record, {r.name}: {value}.",
        "explain": f"{value} on {_day(r.day)}, {best}.",
        "icon": r.icon,
    }


def quest(q: Quest, level: int | None = None, top: int = 6) -> dict[str, Any]:
    """A quest met; with the quest level it led to, which sets the next one."""
    when = "This week's quest" if q.until > q.day else f"{_day(q.day)}'s quest"
    harder = (
        f" Quests get a little harder: level {level} of {top}."
        if level is not None and q.level is not None and level > q.level
        else ""
    )
    return {
        "kind": "quest",
        "quest": q.id,
        "title": f"Quest done: {q.title}",
        "message": f"Quest done, {q.title}: +{q.reward} XP.",
        "explain": f"{when}, met: +{q.reward} XP towards your next level.{harder}",
        "icon": "mdi:flag-checkered",
    }


def recap(m: Month, kept_value: str | None) -> dict[str, Any]:
    summary = (
        f"average score {m.score:g}, {m.nights_lasted} of {m.nights} nights "
        "without the grid"
        + (
            f", {m.kept_kwh:g} kWh of sun kept at home"
            + (f", worth {kept_value}" if kept_value else "")
            if m.kept_kwh is not None
            else ""
        )
    )
    return {
        "kind": "recap",
        "month": m.month,
        "title": f"{m.month} in review",
        "message": f"{m.month}: {summary}.",
        "explain": f"The month just closed: {summary}.",
        "icon": "mdi:calendar-star",
    }


def stamp(moments: Iterable[dict[str, Any]], at: datetime) -> list[dict[str, Any]]:
    """The moments as kept: each with when it happened and an id of its own."""
    when = at.isoformat()
    return [
        {**m, "at": when, "id": f"{m['kind']}:{k}:{when}"}
        for k, m in enumerate(moments)
    ]


def recent(kept: Iterable[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """What is still worth keeping, newest first."""
    oldest = now - KEEP
    out = []
    for m in kept:
        try:
            at = datetime.fromisoformat(str(m["at"]))
        except KeyError, ValueError:
            continue
        if oldest <= at <= now + timedelta(minutes=5):
            out.append((at, m))
    out.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in out[:MAX_KEPT]]
