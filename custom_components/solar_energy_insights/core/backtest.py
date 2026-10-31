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
- Ways of weighting the nights are compared on the same contested evenings, and
  the one in use changes only for a clear gain, so the answers do not flip
  from day to day.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, tzinfo
from enum import StrEnum

from .battery import Battery
from .forecast import PvForecast
from .nights import HourlyUse, past_days, similarity_weights
from .plan import PlanInputs, replay_night

EVENING = time(21, 0)
# A night counts as lasted when it stayed this far above the floor.
LASTED_MARGIN_PCT = 1.0
# Contested evenings needed before anything is learned or switched.
MIN_CONTESTED = 5
# Another weighting must beat the one in use by this share to replace it.
SWITCH_GAIN = 0.10
# The hidden load is searched in these steps, and kept only for this gain.
STANDBY_STEPS_W = tuple(range(0, 160, 10))
STANDBY_GAIN = 0.10
# A reading below the floor, in an hour that otherwise sat this far above it,
# is a glitch rather than a battery asleep at its reserve.
GLITCH_MARGIN_PCT = 5.0


class Weighting(StrEnum):
    EQUAL = "equal"
    SIMILAR = "similar"


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
    weighting: Weighting
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

    weighting: Weighting
    standby_kw: float
    # The score with the chosen weighting and hidden load.
    score: Score
    # Each weighting's honest error: on evenings left out of any fitting.
    honest_error_kwh: dict[Weighting, float | None]
    # The same weighting without a hidden load, for comparison.
    uncorrected: Score

    @property
    def error_kwh(self) -> float | None:
        return self.honest_error_kwh.get(self.weighting)


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
    use: HourlyUse, moment: datetime, tz: tzinfo
) -> dict[tuple[date, int], float]:
    local = moment.astimezone(tz)
    cut = (local.date(), local.hour)
    return {key: kwh for key, kwh in use.items() if key < cut}


def _predict(
    use: HourlyUse,
    tz: tzinfo,
    evening: Evening,
    battery: Battery,
    nights: int,
    weighting: Weighting,
    standby_kw: float,
) -> Scored | None:
    start = datetime.combine(evening.day, EVENING, tz)
    known = _before(use, start, tz)
    days = past_days(known, tz, nights)
    weights = (
        similarity_weights(known, tz, start, days)
        if weighting is Weighting.SIMILAR
        else None
    )
    inputs = PlanInputs(
        now=start,
        tz=tz,
        sunrise=evening.sunrise,
        # After sunrise: the whole replay is night.
        sunset=evening.sunrise + (evening.sunrise - start),
        battery=replace(battery, soc_pct=evening.start_soc),
        pv=PvForecast([]),
        use=known,
        nights=nights,
        weights=weights,
        standby_kw=standby_kw,
    )
    night = replay_night(inputs)
    if night is None:
        return None
    floor = battery.floor_pct
    return Scored(
        day=evening.day,
        predicted_spare_kwh=night.typical.spare_kwh,
        actual_spare_kwh=max(evening.lowest_soc - floor, 0.0)
        / 100
        * battery.capacity_kwh,
        predicted_lasts=night.typical.lasts,
        actual_lasts=evening.lowest_soc > floor + LASTED_MARGIN_PCT,
    )


def score(
    use: HourlyUse,
    tz: tzinfo,
    evenings: Sequence[Evening],
    battery: Battery,
    nights: int,
    weighting: Weighting,
    standby_kw: float = 0.0,
) -> Score:
    """Replay each evening with the given weighting, using only earlier days."""
    scored = tuple(
        s
        for e in evenings
        if (s := _predict(use, tz, e, battery, nights, weighting, standby_kw))
        is not None
    )
    contested = frozenset(n.day for n in scored if n.predicted_lasts or n.actual_lasts)
    return Score(weighting, scored, standby_kw, contested)


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


def calibrate(
    use: HourlyUse,
    tz: tzinfo,
    evenings: Sequence[Evening],
    battery: Battery,
    nights: int,
    current: Weighting = Weighting.EQUAL,
) -> Calibration:
    """Learn the hidden load and choose the weighting from recent evenings."""
    results: dict[Weighting, tuple[Score, Score, float | None]] = {}
    for weighting in Weighting:
        by_step = {
            w: score(use, tz, evenings, battery, nights, weighting, w / 1000)
            for w in STANDBY_STEPS_W
        }
        watts, honest, contested = _fit_standby(by_step)
        fitted = replace(by_step[watts], contested_days=contested)
        uncorrected = replace(by_step[0], contested_days=contested)
        results[weighting] = (fitted, uncorrected, honest)
    honest_errors = {w: r[2] for w, r in results.items()}

    chosen = current
    other = Weighting.SIMILAR if current is Weighting.EQUAL else Weighting.EQUAL
    mine, theirs = honest_errors[current], honest_errors[other]
    enough = len(results[other][0].contested) >= MIN_CONTESTED
    if (
        enough
        and theirs is not None
        and (mine is None or theirs < mine * (1 - SWITCH_GAIN))
    ):
        chosen = other
    fitted, uncorrected, _ = results[chosen]
    return Calibration(
        weighting=chosen,
        standby_kw=fitted.standby_kw,
        score=fitted,
        honest_error_kwh=honest_errors,
        uncorrected=uncorrected,
    )
