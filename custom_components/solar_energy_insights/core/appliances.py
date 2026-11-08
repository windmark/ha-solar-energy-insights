"""When to start an appliance: the start that takes most from free sun.

Sun to spare once the battery is full would be exported, so that part is free.
An appliance draws its energy evenly over its run; each half hour it takes what
free sun there is, up to its own draw. The best start is the earliest one that
takes the largest share of its energy from free sun, from now until sunset.
The dashboard's best-time card works the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from .plan import Plan

HALF_HOUR = timedelta(minutes=30)
# A level this close to the charge limit counts as full.
FULL_WITHIN_PCT = 0.5
# Learned figures outside these are a missed or merged run, not a typical one.
MIN_LEARNED_KWH = 0.05
MAX_LEARNED_FACTOR = 4.0
MIN_LEARNED_HOURS = 0.2
MAX_LEARNED_HOURS = 8.0
LEARNED_RUNS = 5
# Starting now is good when it gets this much from free sun, and about as much
# as the best start does.
GOOD_SHARE = 0.5
GOOD_WITHIN = 0.05


@dataclass(frozen=True, slots=True)
class Run:
    """An appliance's typical run."""

    kwh: float
    hours: float
    # Whether the energy is the typical figure because nothing better is known.
    estimated: bool = False


@dataclass(frozen=True, slots=True)
class Start:
    """The best start until sunset, and what starting now would get."""

    at: datetime
    free_share: float
    # None before sunrise or when the slots are tomorrow's: there is no "now".
    now_share: float | None

    @property
    def good_now(self) -> bool:
        return (
            self.now_share is not None
            and self.now_share >= GOOD_SHARE
            and self.now_share >= self.free_share - GOOD_WITHIN
        )


def learned_run(
    typical_kwh: float,
    typical_hours: float,
    kwh: float | None = None,
    hours: list[float] | None = None,
) -> Run:
    """The run from what a sensor learned, where it is believable, else the
    typical figures: the energy of the last run, the mean of the last durations.
    """
    believable = kwh is not None and MIN_LEARNED_KWH < kwh <= (
        MAX_LEARNED_FACTOR * typical_kwh
    )
    durations = [h for h in hours or [] if MIN_LEARNED_HOURS < h < MAX_LEARNED_HOURS]
    durations = durations[-LEARNED_RUNS:]
    return Run(
        kwh=kwh if believable and kwh is not None else typical_kwh,
        hours=sum(durations) / len(durations) if durations else typical_hours,
        estimated=not believable,
    )


def _level_at(curve: tuple[tuple[datetime, float], ...], t: datetime) -> float:
    level = curve[0][1] if curve else 0.0
    for at, pct in curve:
        if at > t:
            break
        level = pct
    return level


def best_start(plan: Plan, run: Run, tz: tzinfo) -> Start | None:
    """The start that takes the largest share of *run* from free sun, or None
    with no sun left before sunset."""
    day = plan.day
    if day is None or not day.slots or run.kwh <= 0:
        return None
    starts = [t for t, _pv, _load in day.slots]
    free = [
        max(pv - load, 0.0)
        if _level_at(plan.curve, t + timedelta(milliseconds=1))
        >= plan.charge_limit_pct - FULL_WITHIN_PCT
        else 0.0
        for t, pv, load in day.slots
    ]
    count = max(1, round(run.hours * 2))
    draw_kw = run.kwh / (count / 2)

    def share(k: int) -> float:
        got = sum(min(f, draw_kw) / 2 for f in free[k : k + count])
        return min(got / run.kwh, 1.0)

    tomorrow = starts[0].astimezone(tz).date() != plan.now.astimezone(tz).date()
    before_sun = plan.now < starts[0]
    first = next(
        (k for k, t in enumerate(starts) if t + HALF_HOUR > plan.now),
        0,
    )
    best, best_share = first, -1.0
    for k in range(first, len(starts)):
        value = share(k)
        if value > best_share + 1e-9:
            best, best_share = k, value
    return Start(
        at=starts[best],
        free_share=round(best_share, 3),
        now_share=None if tomorrow or before_sun else round(share(first), 3),
    )
