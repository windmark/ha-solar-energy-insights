"""What changed in the plan's answers over the last half hour, and why.

The plan answers afresh whenever the battery, the forecast or the house moves;
a changed answer simply replaces the old one. Here the last half hour of answers
is kept, so a meaningful change can be shown beside the new answer: what it was,
since when, and the likely cause (the house using more than usual, or Solcast
expecting more or less sun). It clears itself once the half hour has passed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

WINDOW = timedelta(minutes=30)
# Smaller moves are the plan's ordinary drift.
TIME_STEP = timedelta(minutes=15)
SPARE_STEP_KWH = 0.3
# A cause must be at least this big to be named.
HOUSE_STEP_KWH = 0.3
FORECAST_STEP_KWH = 0.5
FORECAST_STEP_SHARE = 0.1


@dataclass(frozen=True, slots=True)
class Answers:
    """The answers at one moment, and what they were made from."""

    at: datetime
    outlook: str | None
    grid_from: datetime | None
    spare_kwh: float | None
    full_at: datetime | None
    # The house energy counter, kWh, and the forecast sun left today, kWh.
    house_kwh: float | None
    forecast_kwh: float | None


@dataclass(frozen=True, slots=True)
class Change:
    was: Answers
    since: datetime
    cause: str | None


def _times_differ(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    return abs(a - b) >= TIME_STEP


def _tonight_differs(a: Answers, b: Answers) -> bool:
    if a.outlook != b.outlook or _times_differ(a.grid_from, b.grid_from):
        return True
    if a.spare_kwh is None or b.spare_kwh is None:
        return False
    return abs(a.spare_kwh - b.spare_kwh) >= SPARE_STEP_KWH


def _full_differs(a: Answers, b: Answers) -> bool:
    return _times_differ(a.full_at, b.full_at)


def _cause(was: Answers, now: Answers, usual_kw: float | None) -> str | None:
    """The bigger of the two likely causes, if either is big enough."""
    house: tuple[float, str] | None = None
    if was.house_kwh is not None and now.house_kwh is not None and usual_kw is not None:
        hours = (now.at - was.at).total_seconds() / 3600
        extra = (now.house_kwh - was.house_kwh) - usual_kw * hours
        if abs(extra) >= HOUSE_STEP_KWH:
            more = "more" if extra > 0 else "less"
            house = (abs(extra), f"house used {abs(extra):.1f} kWh {more} than usual")
    sun: tuple[float, str] | None = None
    if was.forecast_kwh is not None and now.forecast_kwh is not None:
        delta = now.forecast_kwh - was.forecast_kwh
        if abs(delta) >= max(FORECAST_STEP_KWH, FORECAST_STEP_SHARE * was.forecast_kwh):
            more = "more" if delta > 0 else "less"
            sun = (abs(delta), f"Solcast now expects {abs(delta):.1f} kWh {more} sun")
    found = [c for c in (house, sun) if c is not None]
    return max(found)[1] if found else None


def detect(
    samples: Sequence[Answers], usual_kw: float | None = None
) -> dict[str, Change]:
    """What changed against the answers half an hour ago: "tonight" and
    "full_at", each with what it was, since when, and why."""
    if len(samples) < 2:
        return {}
    now = samples[-1]
    # The answers as they stood half an hour ago: the last ones at or before
    # then, or the first ones there are.
    before = [s for s in samples if s.at <= now.at - WINDOW]
    was = before[-1] if before else samples[0]
    window = [s for s in samples if s.at >= was.at]
    if len(window) < 2:
        return {}
    out: dict[str, Change] = {}
    for key, differs in (("tonight", _tonight_differs), ("full_at", _full_differs)):
        if not differs(was, now):
            continue
        since = next(s.at for s in window[1:] if differs(was, s))
        out[key] = Change(was=was, since=since, cause=_cause(was, now, usual_kw))
    return out


def keep(samples: list[Answers], new: Answers) -> list[Answers]:
    """The samples still needed: the half hour, and the one just before it."""
    kept = [*samples, new]
    while len(kept) > 2 and kept[1].at <= new.at - WINDOW:
        kept.pop(0)
    return kept
