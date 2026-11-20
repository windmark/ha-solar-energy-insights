"""Seasonal windows, and the editions a seasonal badge is played in.

A seasonal badge can be earned in a window of the year: a season, or a few
weeks around a solstice. Each window is one edition of the badge (Winter
warrior 2026/27), and an edition's bar is the one before it: earn the badge on
clearly more of the days than last winter did and the edition earns a crown.
So the season is the reset, and last year is the opponent. Badges ranked
against your own days earn about the same share every season by design, so
they show the season before for reference but earn no crown.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .profile import (
    days_before_winter_solstice,
    days_from_summer_solstice,
    days_from_winter_solstice,
    season_of,
)

# The windows smaller than a season.
LATE_AUTUMN_DAYS = 42
SOLSTICE_DAYS = 10
# Far enough ahead to reach every window's next opening.
_LOOK_DAYS = 400


def in_season(season: str | None, day: date, southern: bool) -> bool:
    """Whether a badge of this season can be earned on *day*."""
    if season is None:
        return True
    if season == "late autumn":
        return days_before_winter_solstice(day, southern) <= LATE_AUTUMN_DAYS
    if season == "midwinter":
        return days_from_winter_solstice(day, southern) <= SOLSTICE_DAYS
    if season == "midsummer":
        return days_from_summer_solstice(day, southern) <= SOLSTICE_DAYS
    return str(season_of(day, southern)) == season


def window(season: str, day: date, southern: bool) -> tuple[date, date] | None:
    """The window *day* is in, or else the next one: its first and last day.
    Found with the same rule that decides "in season", so the dates shown and
    the badge never disagree."""
    start = day
    if in_season(season, start, southern):
        while in_season(season, start - timedelta(days=1), southern):
            start -= timedelta(days=1)
    else:
        for _ in range(_LOOK_DAYS):
            start += timedelta(days=1)
            if in_season(season, start, southern):
                break
        else:
            return None
    end = start
    while (
        in_season(season, end + timedelta(days=1), southern)
        and (end - start).days < _LOOK_DAYS
    ):
        end += timedelta(days=1)
    return start, end


def label(start: date, end: date) -> str:
    """2026 for a window inside one year, 2026/27 for one across New Year."""
    if start.year == end.year:
        return str(start.year)
    return f"{start.year}/{end.year % 100:02d}"


# A crown asks for clearly more than the season before, not a lucky week.
CROWN_MARGIN = 1.1
CROWN_MIN_DAYS = 2
# A window counts as played when at least this share of its days were judged.
PLAYED_SHARE = 0.9


@dataclass(frozen=True, slots=True)
class Edition:
    start: date
    end: date
    # Days that earned the badge in this window, and the best grade among them.
    days: int
    top: int
    # Not a fair bar to beat: the history began after the window opened, or
    # too few of its days could be judged (missing data, or percentiles not
    # ready yet).
    partial: bool
    # Days judged in the window so far.
    judged: int = 0
    # The earning days this edition needs for a crown; None when it can't get one.
    bar: int | None = None
    # The day it reached the bar, if it did.
    crowned_on: date | None = None

    @property
    def label(self) -> str:
        return label(self.start, self.end)

    @property
    def length(self) -> int:
        return (self.end - self.start).days + 1


def to_beat(before: Edition | None, length: int) -> int | None:
    """The earning days a window of *length* days needs to beat the edition
    before: a share of its days at least a tenth above that one's share, and
    at least two days more. None when there is no fair bar: no edition before,
    one not played in full, or one that earned nothing (a first earning day is
    the tier's to reward, not a crown's)."""
    if before is None or before.partial or before.days == 0 or not before.judged:
        return None
    share = before.days / before.judged
    return max(before.days + CROWN_MIN_DAYS, math.ceil(share * CROWN_MARGIN * length))


def editions(
    season: str,
    earned: Sequence[tuple[date, int]],
    judged: Sequence[date],
    first: date,
    today: date,
    southern: bool,
    crowns: bool = True,
) -> list[Edition]:
    """Every window from the first day of history to today, oldest first, with
    what was earned in it. *earned* holds each earning day and its grade,
    *judged* every day the badge could be judged on.

    With *crowns*, an edition is crowned on the day it reaches its bar (see
    to_beat), as long as it is being played in full itself: at least nine in
    ten of its days so far judged."""
    out: list[Edition] = []
    day = first
    while day <= today:
        found = window(season, day, southern)
        if found is None or found[0] > today:
            break
        start, end = found
        inside = sorted((d, g) for d, g in earned if start <= d <= end)
        seen = sorted(d for d in judged if start <= d <= end)
        last_day = min(end, today)
        elapsed = (last_day - max(start, first)).days + 1
        partial = start < first or len(seen) < PLAYED_SHARE * max(elapsed, 1)
        length = (end - start).days + 1
        bar = to_beat(out[-1] if out else None, length)
        # No crown without crowns, in a window history began in the middle
        # of, or past what the window's days allow.
        if not crowns or start < first or (bar is not None and bar > length):
            bar = None
        crowned_on = None
        if bar is not None and len(inside) >= bar:
            on = inside[bar - 1][0]
            # Played in full up to that day.
            so_far = sum(1 for d in seen if d <= on)
            if so_far >= PLAYED_SHARE * ((on - start).days + 1):
                crowned_on = on
        out.append(
            Edition(
                start,
                end,
                len(inside),
                max((g for _, g in inside), default=0),
                partial=partial,
                judged=len(seen),
                bar=bar,
                crowned_on=crowned_on,
            )
        )
        day = end + timedelta(days=1)
    return out
