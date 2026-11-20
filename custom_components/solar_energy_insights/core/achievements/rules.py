"""What a badge is, and the few kinds of rule badges are made of.

A day's rule gives a grade: 0 when the day did not earn the badge, else the
highest step of its ladder the day reached (1 to 3). A badge without a ladder
gives 1 for an earning day. Tier k then needs a number of days at step k or
above, so a badge gets harder as it climbs: Winter warrior wants one day at
50 %, then five at 65 %, then ten at 80 %.

Thresholds that describe a good day are percentiles of the days *before* the
one judged, so the whole history replays the same way and nothing peeks ahead.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta, tzinfo

from .facts import DayFacts
from .profile import Capability, Profile, Season, season_of

# Days of history before a percentile threshold is trusted.
MIN_PAST = 14
STEPS = 3

type Value = Callable[[DayFacts], float | None]
type Grade = Callable[[DayFacts, "Context"], int | None]
type Count = Callable[[Sequence[DayFacts], "Context"], float]


def sorted_quantile(values: Sequence[float], share: float) -> float:
    """A percentile of sorted values, between neighbours as numpy's default."""
    at = share * (len(values) - 1)
    lo = int(at)
    hi = min(lo + 1, len(values) - 1)
    return float(values[lo] + (values[hi] - values[lo]) * (at - lo))


class Memo:
    """The past values a percentile is taken from, kept sorted between days so
    a replay doesn't go through the whole history again for each day."""

    def __init__(self) -> None:
        self._seen: dict[object, tuple[int, list[float]]] = {}

    def values(
        self,
        key: object,
        past: Sequence[DayFacts],
        value: Value,
        keep: Callable[[DayFacts], bool],
    ) -> list[float]:
        seen, values = self._seen.get(key, (0, []))
        if seen > len(past):  # a shorter history: start again
            seen, values = 0, []
        for f in past[seen:]:
            if keep(f) and (v := value(f)) is not None:
                bisect.insort(values, v)
        self._seen[key] = (len(past), values)
        return values


class Context:
    """What a rule may know on a day: the home, and the days before it."""

    def __init__(
        self,
        profile: Profile,
        tz: tzinfo,
        past: Sequence[DayFacts],
        by_day: Mapping[date, DayFacts] | None = None,
        memo: Memo | None = None,
        since: date | None = None,
    ) -> None:
        self.profile = profile
        self.tz = tz
        self.past = past
        self._by_day = by_day or {}
        self._memo = memo
        # A successor's unlock: what came before doesn't count towards it.
        self.since = since

    def counts(self, f: DayFacts) -> bool:
        """Whether an earlier day may count towards a run or a week."""
        return self.since is None or f.day > self.since

    def season(self, day: date) -> Season:
        return season_of(day, self.profile.southern)

    def earlier(self, day: date, days: int) -> DayFacts | None:
        """The day *days* before *day*, if history holds it."""
        return self._by_day.get(day - timedelta(days=days))

    def quantiles(
        self,
        value: Value,
        shares: Sequence[float],
        season: Season | None = None,
        key: object = None,
    ) -> tuple[float, ...] | None:
        """Percentiles of the days before, in one season or all year; None
        until there are enough of them. With a *key* naming the value, kept
        between days of one replay."""

        def keep(f: DayFacts) -> bool:
            return season is None or self.season(f.day) == season

        if key is not None and self._memo is not None:
            values = self._memo.values((key, season), self.past, value, keep)
        else:
            values = sorted(
                v for f in self.past if keep(f) and (v := value(f)) is not None
            )
        if len(values) < MIN_PAST:
            return None
        return tuple(sorted_quantile(values, s) for s in shares)

    def quantile(
        self,
        value: Value,
        share: float,
        season: Season | None = None,
        key: object = None,
    ) -> float | None:
        found = self.quantiles(value, (share,), season, key)
        return None if found is None else found[0]

    def specific_yield(self, f: DayFacts) -> float | None:
        """Yield per kWp; without a known size, the yield itself, which ranks
        days the same way against the home's own history."""
        if f.solar_kwh is None:
            return None
        return f.solar_kwh / self.profile.kwp if self.profile.kwp else f.solar_kwh


@dataclass(frozen=True, slots=True)
class Badge:
    id: str
    name: str
    icon: str
    description: str
    needs: tuple[Capability, ...]
    # Days (or, for counts, the total) each tier needs.
    tiers: tuple[int, int, int]
    # What a day must reach for each tier, as shown ("65 %"); None: one bar.
    steps: tuple[str, str, str] | None = None
    # A season, or a window inside one; None counts all year.
    season: str | None = None
    hidden: bool = False
    riddle: str = ""
    # A day's grade; or, for runs and milestones, a count over the history.
    grade: Grade | None = None
    count: Count | None = None
    # Offered once this badge is complete, and counted from then on.
    follows: str | None = None
    # Seasons beat the season before for a crown. Off for badges ranked
    # against your own days, which earn about the same share every season.
    crowns: bool = True
    # What a count counts; graded badges count days.
    unit: str = "day"

    def level(self, tier: int) -> int:
        """The grade a day needs to count towards *tier* (1 to 3)."""
        return tier if self.steps else 1


# Rule builders. Each returns a Grade; None means the day can't be judged.


def flat(rule: Callable[[DayFacts, Context], bool | None]) -> Grade:
    """A day earns it or not."""

    def grade(f: DayFacts, c: Context) -> int | None:
        verdict = rule(f, c)
        return None if verdict is None else int(verdict)

    return grade


def steps_met(value: float, bars: Sequence[float], lowest: bool = False) -> int:
    """How many of the ladder's bars *value* clears, in order."""
    met = 0
    for bar in bars:
        if (value <= bar) if lowest else (value >= bar):
            met += 1
        else:
            break
    return met


def at_least(value: Value, bars: tuple[float, float, float]) -> Grade:
    """A ladder of fixed bars: 50 %, 65 %, 80 %."""

    def grade(f: DayFacts, _c: Context) -> int | None:
        v = value(f)
        return None if v is None else steps_met(v, bars)

    return grade


def top(
    value: Callable[[DayFacts, Context], float | None],
    shares: tuple[float, float, float],
    season: Season | None = None,
    lowest: bool = False,
) -> Grade:
    """A ladder against your own days: in your top 25 %, 10 %, 3 % (of the
    season's days, if given). Lower is better with *lowest*."""

    key = object()

    def grade(f: DayFacts, c: Context) -> int | None:
        v = value(f, c)
        if v is None:
            return None
        cuts = c.quantiles(
            lambda d: value(d, c),
            [s if lowest else 1 - s for s in shares],
            season,
            key,
        )
        if cuts is None:
            return None
        return steps_met(v, cuts, lowest)

    return grade


def run(
    holds: Callable[[DayFacts, Context], bool | None],
    lengths: tuple[int, int, int],
) -> Grade:
    """Days in a row: graded on the day a run reaches each length, so a run of
    ten earns the step for 7 on its seventh day and the one for 10 on its
    tenth. A missing day breaks a run."""

    def grade(f: DayFacts, c: Context) -> int | None:
        today = holds(f, c)
        if today is None:
            return None
        if not today:
            return 0
        length, day = 1, f.day
        for before in reversed(c.past):
            if (
                (day - before.day).days != 1
                or not c.counts(before)
                or not holds(before, c)
            ):
                break
            length, day = length + 1, before.day
        return lengths.index(length) + 1 if length in lengths else 0

    return grade


def in_window(inside: Callable[[date, Profile], bool], rule: Grade) -> Grade:
    """Only days inside a window of the year count; the rest grade 0."""

    def grade(f: DayFacts, c: Context) -> int | None:
        if not inside(f.day, c.profile):
            return 0
        return rule(f, c)

    return grade


def seasonal(season: Season, rule: Grade) -> Grade:
    """Only days in *season* count; the rest grade 0."""
    return in_window(lambda d, p: season_of(d, p.southern) == season, rule)
