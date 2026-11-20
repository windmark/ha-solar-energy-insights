"""Seasonal windows, and the editions a seasonal badge is played in.

A seasonal badge can be earned in a window of the year: a season, or a few
weeks around a solstice. Each window is one edition of the badge (Winter
warrior 2026/27), and an edition's bar is the one before it: beat last
winter's days and the edition earns a crown. So the season is the reset, and
last year is the opponent.
"""

from __future__ import annotations

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


@dataclass(frozen=True, slots=True)
class Edition:
    start: date
    end: date
    # Days that earned the badge in this window, and the best grade among them.
    days: int
    top: int
    # The history began after the window opened: not a fair bar to beat.
    partial: bool
    # The day it passed the edition before it, if it did.
    crowned_on: date | None = None

    @property
    def label(self) -> str:
        return label(self.start, self.end)


def to_beat(before: Edition | None) -> int | None:
    """The days an edition needs to beat the one before: one more than it
    earned. None when there is no fair bar: no edition before, one the history
    began in the middle of, or one that earned nothing (a first earning day is
    the tier's to reward, not a crown's)."""
    if before is None or before.partial or before.days == 0:
        return None
    return before.days + 1


def editions(
    season: str,
    earned: Sequence[tuple[date, int]],
    first: date,
    today: date,
    southern: bool,
) -> list[Edition]:
    """Every window from the first day of history to today, oldest first, with
    what was earned in it. *earned* holds each earning day and its grade.

    An edition is crowned on the day its count passes the edition before, as
    long as that one was played in full and earned the badge at all: a first
    earning day is the tier's to reward, not a crown's."""
    out: list[Edition] = []
    day = first
    while day <= today:
        found = window(season, day, southern)
        if found is None or found[0] > today:
            break
        start, end = found
        inside = sorted((d, g) for d, g in earned if start <= d <= end)
        crowned_on = None
        bar = to_beat(out[-1] if out else None)
        if bar is not None and len(inside) >= bar:
            crowned_on = inside[bar - 1][0]
        out.append(
            Edition(
                start,
                end,
                len(inside),
                max((g for _, g in inside), default=0),
                partial=start < first,
                crowned_on=crowned_on,
            )
        )
        day = end + timedelta(days=1)
    return out
