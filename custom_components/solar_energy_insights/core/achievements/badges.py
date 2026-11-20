"""Badges: the catalogue played over the history.

Each day gets a grade from each badge's rule (rules.py); tiers count the days
at each step of the ladder. A badge that follows another is offered once that
one is complete, and counts only the days after. Seasonal badges are also
played in editions, one per window of the year, each against the one before
(editions.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta, tzinfo

from .catalogue import CATALOGUE
from .editions import Edition, editions
from .facts import DayFacts
from .profile import Profile
from .rules import STEPS, Badge, Context, Memo

__all__ = ["CATALOGUE", "CROWN_XP", "TIERS", "TIER_XP", "BadgeState", "evaluate"]

TIERS = ("bronze", "silver", "gold")
TIER_XP = (50, 100, 200)
# For each edition that beat the one before.
CROWN_XP = 150
RARE, LEGENDARY = 0.10, 0.02


@dataclass(frozen=True, slots=True)
class BadgeState:
    badge: Badge
    # Towards the next tier (at its step of the ladder), or the last tier's.
    count: float
    tier: int
    next_at: int | None
    # Share of the days it applied to that earned it; None for runs and milestones.
    rarity: float | None
    earned_on: tuple[date, ...]
    tier_days: tuple[date, ...]
    # Days at each step of the ladder or above; None without a ladder.
    step_days: tuple[int, int, int] | None = None
    # Days it applied to, for each step's rarity.
    applied: int = 0
    editions: tuple[Edition, ...] = ()
    # Follows a badge that is not complete yet.
    locked: bool = False

    @property
    def rarity_name(self) -> str | None:
        if self.rarity is None or self.count == 0:
            return None
        if self.rarity < LEGENDARY:
            return "legendary"
        if self.rarity < RARE:
            return "rare"
        return "common"

    @property
    def crowns(self) -> int:
        return sum(1 for e in self.editions if e.crowned_on is not None)

    @property
    def crowned_on(self) -> tuple[date, ...]:
        return tuple(e.crowned_on for e in self.editions if e.crowned_on is not None)


def evaluate(
    facts: Sequence[DayFacts],
    profile: Profile,
    tz: tzinfo,
    today: date | None = None,
) -> list[BadgeState]:
    """Every badge the home can earn, with its count, tier and rarity."""
    by_day = {f.day: f for f in facts}
    today = today or (facts[-1].day if facts else None)
    out: dict[str, BadgeState] = {}
    for badge in CATALOGUE:
        if not profile.has(*badge.needs):
            continue
        after = None
        if badge.follows is not None:
            before = out.get(badge.follows)
            if before is None:
                continue
            if before.tier < STEPS:
                out[badge.id] = BadgeState(
                    badge, 0, 0, badge.tiers[0], None, (), (), locked=True
                )
                continue
            after = before.tier_days[-1]
        if badge.grade is not None:
            state = _graded(badge, facts, profile, tz, by_day, after, today)
        else:
            state = _counted(badge, facts, profile, tz)
        out[badge.id] = state
    return list(out.values())


def _graded(
    badge: Badge,
    facts: Sequence[DayFacts],
    profile: Profile,
    tz: tzinfo,
    by_day: dict[date, DayFacts],
    after: date | None,
    today: date | None,
) -> BadgeState:
    assert badge.grade is not None
    graded: list[tuple[date, int]] = []
    applied = 0
    at_step = [0] * STEPS
    tier_days: list[date] = []
    memo = Memo()
    for k, f in enumerate(facts):
        if after is not None and f.day <= after:
            continue
        grade = badge.grade(f, Context(profile, tz, facts[:k], by_day, memo))
        if grade is None:
            continue
        applied += 1
        if not grade:
            continue
        graded.append((f.day, grade))
        for step in range(min(grade, STEPS)):
            at_step[step] += 1
        # Tiers in order, several on one day if it reached them all.
        while len(tier_days) < STEPS:
            nxt = len(tier_days)
            if at_step[badge.level(nxt + 1) - 1] < badge.tiers[nxt]:
                break
            tier_days.append(f.day)
    tier = len(tier_days)
    count = at_step[badge.level(min(tier + 1, STEPS)) - 1]
    played: tuple[Edition, ...] = ()
    if badge.season is not None and facts and today is not None:
        # A successor's editions start when it was unlocked.
        first = facts[0].day if after is None else after + timedelta(days=1)
        played = tuple(editions(badge.season, graded, first, today, profile.southern))
    return BadgeState(
        badge=badge,
        count=count,
        tier=tier,
        next_at=badge.tiers[tier] if tier < STEPS else None,
        rarity=len(graded) / applied if applied else None,
        earned_on=tuple(d for d, _ in graded),
        tier_days=tuple(tier_days),
        step_days=(at_step[0], at_step[1], at_step[2]) if badge.steps else None,
        applied=applied,
        editions=played,
    )


def _counted(
    badge: Badge, facts: Sequence[DayFacts], profile: Profile, tz: tzinfo
) -> BadgeState:
    assert badge.count is not None
    count = badge.count(facts, Context(profile, tz, facts))
    # When each tier was reached: counts only grow as days are added, so the
    # first day a prefix reaches a tier is found by halving.
    tier_days: list[date] = []
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
    return BadgeState(
        badge=badge,
        count=round(count, 2),
        tier=tier,
        next_at=badge.tiers[tier] if tier < STEPS else None,
        rarity=None,
        earned_on=(),
        tier_days=tuple(tier_days),
    )
