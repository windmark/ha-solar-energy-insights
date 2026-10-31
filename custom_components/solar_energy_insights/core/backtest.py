"""How well would the night answer have done? Replay the recent evenings.

For each evening, the battery as it was at 21:00 is replayed against the nights
before it, exactly as the live plan would have, and compared with the lowest
state of charge it actually reached before sunrise. No prediction has to be
stored: the recorder holds everything needed.

Three lessons from real data shape what is scored and learned:

- An evening that starts nearly empty ends at the reserve whatever the model
  says. Only evenings where the prediction or the battery said "lasted" are
  contested; the rest would flatter any score.
- The house counter does not see everything the battery feeds, such as the
  inverter's own standby. One constant hidden load is learned, and kept only if
  it predicts evenings it was not fitted on better (leave one out).
- Every recent night counts the same. Weighting nights by how similar they
  looked (to the day so far, the day of the week, temperature or presence) was
  tried and dropped: on a month of real evenings it predicted no better.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, tzinfo

import numpy as np

from . import batch
from .batch import Bools, Floats, Slices
from .battery import Battery
from .nights import HourlySeries, HourlyUse, PastDay, past_days
from .plan import HEAVY_SHARE

EVENING = time(21, 0)
# A night counts as lasted when it stayed this far above the floor.
LASTED_MARGIN_PCT = 1.0
# Contested evenings needed before anything is learned or switched.
MIN_CONTESTED = 5
# The hidden load is searched in these steps, and kept only for this gain.
STANDBY_STEPS_W = tuple(range(0, 160, 10))
STANDBY_GAIN = 0.10
# A reading below the floor, in an hour that otherwise sat this far above it,
# is a glitch rather than a battery asleep at its reserve.
GLITCH_MARGIN_PCT = 5.0
# Contested evenings needed to calibrate how heavy "a heavy night" is, and the
# range it is kept in.
MIN_BAND_EVENINGS = 12
HEAVY_SHARE_RANGE = (0.6, 0.95)


@dataclass(frozen=True, slots=True)
class Evening:
    """What the battery did on one past night."""

    day: date
    start_soc: float
    sunrise: datetime
    lowest_soc: float


@dataclass(frozen=True, slots=True)
class Scored:
    day: date
    predicted_spare_kwh: float
    actual_spare_kwh: float
    predicted_lasts: bool
    actual_lasts: bool

    @property
    def error_kwh(self) -> float:
        """Predicted minus actual spare: positive is optimistic."""
        return self.predicted_spare_kwh - self.actual_spare_kwh


@dataclass(frozen=True, slots=True)
class Score:
    nights: tuple[Scored, ...]
    standby_kw: float = 0.0
    # Days of the evenings that could have gone either way.
    contested_days: frozenset[date] = frozenset()

    @property
    def contested(self) -> tuple[Scored, ...]:
        return tuple(n for n in self.nights if n.day in self.contested_days)

    @property
    def error_kwh(self) -> float | None:
        """Mean absolute error of the spare at sunrise, on contested evenings."""
        nights = self.contested
        return sum(abs(n.error_kwh) for n in nights) / len(nights) if nights else None

    @property
    def bias_kwh(self) -> float | None:
        """Mean signed error on contested evenings: positive is optimistic."""
        nights = self.contested
        return sum(n.error_kwh for n in nights) / len(nights) if nights else None

    @property
    def agreement(self) -> float | None:
        """Share of contested evenings where "lasts the night" was right."""
        nights = self.contested
        if not nights:
            return None
        return sum(n.predicted_lasts == n.actual_lasts for n in nights) / len(nights)


@dataclass(frozen=True, slots=True)
class Calibration:
    """What was learned from the recent evenings, and how well it does."""

    standby_kw: float
    # The score with the learned hidden load.
    score: Score
    # The honest error: on evenings left out of any fitting.
    honest_error_kwh: float | None
    # The same evenings without a hidden load, for comparison.
    uncorrected: Score
    # Where "a heavy night" sits among the replayed nights, calibrated so it is
    # exceeded about as often on real nights as intended.
    heavy_share: float = HEAVY_SHARE

    @property
    def error_kwh(self) -> float | None:
        return self.honest_error_kwh


def clean_lows(
    hours: Mapping[datetime, tuple[float | None, float | None]], floor_pct: float
) -> tuple[dict[datetime, float], int]:
    """The battery's lowest level per hour, without impossible readings.

    *hours* maps each hour to its (lowest, mean) state of charge. A battery
    cannot go below its floor: such a reading in an hour that otherwise sat well
    above the floor is a glitch, and the hour's mean stands in. Otherwise the
    battery was asleep at its reserve, which some inverters report as 0 %.
    Returns the cleaned levels and how many hours needed correcting.
    """
    cleaned: dict[datetime, float] = {}
    corrected = 0
    for start, (low, mean) in hours.items():
        if low is None:
            continue
        if low >= floor_pct - 0.5:
            cleaned[start] = low
            continue
        corrected += 1
        if mean is not None and mean >= floor_pct + GLITCH_MARGIN_PCT:
            cleaned[start] = mean
        else:
            cleaned[start] = floor_pct
    return cleaned, corrected


def _before(
    use: HourlySeries, moment: datetime, tz: tzinfo
) -> dict[tuple[date, int], float]:
    local = moment.astimezone(tz)
    cut = (local.date(), local.hour)
    return {key: value for key, value in use.items() if key < cut}


@dataclass(frozen=True, slots=True)
class _EveningRuns:
    """One past evening, replayed against each night before it, for every
    hidden load at once: arrays of (hidden load, night)."""

    evening: Evening
    start: datetime
    known: dict[tuple[date, int], float]
    days: list[PastDay]
    spare: Floats
    lasts: Bools
    badness: Floats


def _replay_evening(
    use: HourlyUse,
    tz: tzinfo,
    evening: Evening,
    battery: Battery,
    nights: int,
    steps_kw: Sequence[float],
) -> _EveningRuns | None:
    start = datetime.combine(evening.day, EVENING, tz)
    known = _before(use, start, tz)
    days = past_days(known, tz, nights)
    if not days:
        return None
    slices = Slices.between(start, evening.sunrise, None)
    base = np.array([slices.sample(day.rate) for day in days])
    loads = np.concatenate([base + kw for kw in steps_kw])
    runs = batch.run(
        battery,
        np.full(len(loads), evening.start_soc),
        slices,
        np.zeros(len(slices)),
        loads,
    )
    shape = (len(steps_kw), len(days))
    spare = runs.spare_kwh.reshape(shape)
    grid = runs.grid_kwh.reshape(shape)
    return _EveningRuns(
        evening,
        start,
        known,
        days,
        spare,
        runs.lasts.reshape(shape),
        np.where(grid > 0, grid, -spare),
    )


def _typical(row: Floats, weights: Floats) -> int:
    """The night at the weighted middle of how the nights ended."""
    target = batch.weighted_quantile(row, weights, 0.5)
    return int(np.argmin(np.abs(row - target) - weights * 1e-9))


def _scored(r: _EveningRuns, step: int, weights: Floats, battery: Battery) -> Scored:
    k = _typical(r.badness[step], weights)
    floor = battery.floor_pct
    return Scored(
        day=r.evening.day,
        predicted_spare_kwh=float(r.spare[step, k]),
        actual_spare_kwh=max(r.evening.lowest_soc - floor, 0.0)
        / 100
        * battery.capacity_kwh,
        predicted_lasts=bool(r.lasts[step, k]),
        actual_lasts=r.evening.lowest_soc > floor + LASTED_MARGIN_PCT,
    )


def _equal(r: _EveningRuns) -> Floats:
    return np.ones(len(r.days))


def _score(runs: Sequence[_EveningRuns], step: int, battery: Battery) -> Score:
    scored = tuple(_scored(r, step, _equal(r), battery) for r in runs)
    contested = frozenset(n.day for n in scored if n.predicted_lasts or n.actual_lasts)
    return Score(scored, STANDBY_STEPS_W[step] / 1000, contested)


def score(
    use: HourlyUse,
    tz: tzinfo,
    evenings: Sequence[Evening],
    battery: Battery,
    nights: int,
    standby_kw: float = 0.0,
) -> Score:
    """Replay each evening with a hidden load, using only the days before it."""
    runs = [
        r
        for e in evenings
        if (r := _replay_evening(use, tz, e, battery, nights, [standby_kw])) is not None
    ]
    scored = tuple(_scored(r, 0, _equal(r), battery) for r in runs)
    contested = frozenset(n.day for n in scored if n.predicted_lasts or n.actual_lasts)
    return Score(scored, standby_kw, contested)


def _mae(rows: Sequence[Scored], days: frozenset[date]) -> float | None:
    chosen = [abs(r.error_kwh) for r in rows if r.day in days]
    return sum(chosen) / len(chosen) if chosen else None


def _fit_standby(
    by_step: dict[int, Score],
) -> tuple[int, float | None, frozenset[date]]:
    """The hidden load that predicts left-out evenings best, if it helps at all.

    Returns the load in watts (0 if it does not help), the honest error, and the
    contested evenings it was judged on.
    """
    # An evening is contested if it lasted, or any of the loads tried said so.
    contested = frozenset().union(*(s.contested_days for s in by_step.values()))
    rows = {step: {n.day: n for n in s.nights} for step, s in by_step.items()}
    baseline = _mae(by_step[0].nights, contested)
    if baseline is None or len(contested) < MIN_CONTESTED:
        return 0, baseline, contested
    held_out = []
    for day in sorted(contested):
        rest = contested - {day}
        best = min(
            STANDBY_STEPS_W, key=lambda w: (_mae(by_step[w].nights, rest) or 0.0, w)
        )
        held_out.append(abs(rows[best][day].error_kwh))
    honest = sum(held_out) / len(held_out)
    if honest >= baseline * (1 - STANDBY_GAIN):
        return 0, baseline, contested
    fitted = min(
        STANDBY_STEPS_W, key=lambda w: (_mae(by_step[w].nights, contested) or 0.0, w)
    )
    return fitted, honest, contested


def _heavy_share(runs: Sequence[_EveningRuns], step: int, score: Score) -> float:
    """Where a heavy night should sit among the replayed nights.

    For each contested evening, find where the real night fell among the
    replayed ones (0 = better than all, 1 = worse than all). "Heavy" should be
    worse than the real night about as often as intended: the 80th percentile
    of those positions. A night that ran out is only known to be at least as
    bad as every replay that ran out, so it is placed halfway into those.
    """
    contested = score.contested_days
    positions = []
    by_day = {n.day: n for n in score.nights}
    for r in runs:
        w = _equal(r)
        n = by_day.get(r.evening.day)
        if n is None or n.day not in contested:
            continue
        badness, total = r.badness[step], w.sum()
        if n.actual_lasts:
            positions.append(w[badness <= -n.actual_spare_kwh].sum() / total)
        else:
            lasting = w[badness <= 0].sum() / total
            positions.append((lasting + 1) / 2)
    if len(positions) < MIN_BAND_EVENINGS:
        return HEAVY_SHARE
    low, high = HEAVY_SHARE_RANGE
    return float(np.clip(np.quantile(positions, HEAVY_SHARE), low, high))


def calibrate(
    use: HourlyUse,
    tz: tzinfo,
    evenings: Sequence[Evening],
    battery: Battery,
    nights: int,
) -> Calibration:
    """Learn from recent evenings: the hidden load, and how heavy a heavy night is.

    Each evening is replayed once, against every past night and every hidden
    load at once.
    """
    steps_kw = [w / 1000 for w in STANDBY_STEPS_W]
    runs = [
        r
        for e in evenings
        if (r := _replay_evening(use, tz, e, battery, nights, steps_kw)) is not None
    ]
    by_step = {w: _score(runs, k, battery) for k, w in enumerate(STANDBY_STEPS_W)}
    watts, honest, contested = _fit_standby(by_step)
    fitted = replace(by_step[watts], contested_days=contested)
    return Calibration(
        standby_kw=fitted.standby_kw,
        score=fitted,
        honest_error_kwh=honest,
        uncorrected=replace(by_step[0], contested_days=contested),
        heavy_share=_heavy_share(runs, STANDBY_STEPS_W.index(watts), fitted),
    )
