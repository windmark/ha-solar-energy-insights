"""How well would the night answer have done? Replay the recent evenings.

For each evening, the battery as it was at 21:00 is replayed against the nights
before it, exactly as the live plan would have, and compared with the lowest
state of charge it actually reached before sunrise. No prediction has to be
stored: the recorder holds everything needed, so the score is there from the
first day and can compare ways of weighting the nights.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
# Similarity must beat equal weights by this share, on at least this many nights.
MIN_GAIN = 0.05
MIN_NIGHTS = 7


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


@dataclass(frozen=True, slots=True)
class Score:
    weighting: Weighting
    nights: tuple[Scored, ...]

    @property
    def error_kwh(self) -> float | None:
        """Mean absolute error of the spare energy at sunrise."""
        if not self.nights:
            return None
        return sum(
            abs(n.predicted_spare_kwh - n.actual_spare_kwh) for n in self.nights
        ) / len(self.nights)

    @property
    def agreement(self) -> float | None:
        """Share of nights where "lasts the night" was right."""
        if not self.nights:
            return None
        return sum(n.predicted_lasts == n.actual_lasts for n in self.nights) / len(
            self.nights
        )


def _before(
    use: HourlyUse, moment: datetime, tz: tzinfo
) -> dict[tuple[date, int], float]:
    local = moment.astimezone(tz)
    cut = (local.date(), local.hour)
    return {key: kwh for key, kwh in use.items() if key < cut}


def score(
    use: HourlyUse,
    tz: tzinfo,
    evenings: Sequence[Evening],
    battery: Battery,
    nights: int,
    weighting: Weighting,
) -> Score:
    """Replay each evening with the given weighting, using only earlier days."""
    scored = []
    for evening in evenings:
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
        )
        night = replay_night(inputs)
        if night is None:
            continue
        floor = battery.floor_pct
        scored.append(
            Scored(
                day=evening.day,
                predicted_spare_kwh=night.typical.spare_kwh,
                actual_spare_kwh=max(evening.lowest_soc - floor, 0.0)
                / 100
                * battery.capacity_kwh,
                predicted_lasts=night.typical.lasts,
                actual_lasts=evening.lowest_soc > floor + LASTED_MARGIN_PCT,
            )
        )
    return Score(weighting, tuple(scored))


def choose(
    scores: Callable[[Weighting], Score],
) -> tuple[Weighting, dict[Weighting, Score]]:
    """Similarity weighting is used only once it clearly beats equal weights."""
    results = {w: scores(w) for w in Weighting}
    equal, similar = results[Weighting.EQUAL], results[Weighting.SIMILAR]
    if (
        len(similar.nights) >= MIN_NIGHTS
        and equal.error_kwh is not None
        and similar.error_kwh is not None
        and similar.error_kwh < equal.error_kwh * (1 - MIN_GAIN)
    ):
        return Weighting.SIMILAR, results
    return Weighting.EQUAL, results
