"""Score the night answer on recent evenings, replayed from the recorder.

Each evening's 21:00 battery is replayed against the nights before it and
compared with the lowest level it really reached before sunrise.

- Only contested evenings are scored (predicted or actually lasted): one that
  starts nearly empty ends at the reserve whatever the model says, and would
  flatter any score.
- The house meter misses some load (the inverter's standby). One constant is
  learned, and kept only if it predicts left-out evenings better.
- Nights count equally: weighting them by similarity was tried on a month of
  real evenings and predicted no better.
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
LASTED_ABOVE_FLOOR_PCT = 1.0
MIN_CONTESTED_EVENINGS = 5
STANDBY_STEPS_W = tuple(range(0, 160, 10))
MIN_STANDBY_GAIN = 0.10
# A reading below the floor in an hour that otherwise sat this far above it is
# a glitch, not a battery asleep at its reserve.
GLITCH_MARGIN_PCT = 5.0
MIN_EVENINGS_FOR_HEAVY_SHARE = 12
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
    score: Score
    # On evenings left out of any fitting.
    honest_error_kwh: float | None
    # Without the hidden load, for comparison.
    uncorrected: Score
    # Calibrated so a heavy night is exceeded as often as intended.
    heavy_share: float = HEAVY_SHARE

    @property
    def error_kwh(self) -> float | None:
        return self.honest_error_kwh


def clean_lows(
    hours: Mapping[datetime, tuple[float | None, float | None]], floor_pct: float
) -> tuple[dict[datetime, float], int]:
    """The lowest level per hour, with glitches replaced by the hour's mean.

    *hours* maps each hour to its (lowest, mean) state of charge. Returns the
    cleaned levels and how many hours were corrected.
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
        actual_lasts=r.evening.lowest_soc > floor + LASTED_ABOVE_FLOOR_PCT,
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
    if baseline is None or len(contested) < MIN_CONTESTED_EVENINGS:
        return 0, baseline, contested
    held_out = []
    for day in sorted(contested):
        rest = contested - {day}
        best = min(
            STANDBY_STEPS_W, key=lambda w: (_mae(by_step[w].nights, rest) or 0.0, w)
        )
        held_out.append(abs(rows[best][day].error_kwh))
    honest = sum(held_out) / len(held_out)
    if honest >= baseline * (1 - MIN_STANDBY_GAIN):
        return 0, baseline, contested
    fitted = min(
        STANDBY_STEPS_W, key=lambda w: (_mae(by_step[w].nights, contested) or 0.0, w)
    )
    return fitted, honest, contested


def _heavy_share(runs: Sequence[_EveningRuns], step: int, score: Score) -> float:
    """Where a heavy night should sit among the replayed nights.

    The 80th percentile of where real nights fell among their replays
    (0 = best, 1 = worst). A night that ran out is only known to be at least as
    bad as the replays that ran out, so it is placed halfway into them.
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
    if len(positions) < MIN_EVENINGS_FOR_HEAVY_SHARE:
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
