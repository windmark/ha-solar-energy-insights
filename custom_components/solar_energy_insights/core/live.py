"""Score the plan's own answers, saved at the time, against what happened.

The backtest replays what the plan would have said; this checks what it did
say. The night answer is saved at 21:00, "full at" in the morning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, tzinfo

FULL_PCT = 99.0


@dataclass(frozen=True, slots=True)
class NightAnswer:
    """What the plan said at 21:00."""

    spare_kwh: float
    lasts: bool


@dataclass(frozen=True, slots=True)
class NightScore:
    nights: int
    error_kwh: float | None
    lasts_right: float | None


def score_nights(
    answers: Mapping[date, NightAnswer],
    actual_spare_kwh: Mapping[date, float],
    actual_lasts: Mapping[date, bool],
) -> NightScore:
    """On evenings that could have gone either way, as in the backtest."""
    days = [
        d
        for d in sorted(answers)
        if d in actual_spare_kwh and (answers[d].lasts or actual_lasts[d])
    ]
    if not days:
        return NightScore(0, None, None)
    errors = [abs(answers[d].spare_kwh - actual_spare_kwh[d]) for d in days]
    right = [answers[d].lasts == actual_lasts[d] for d in days]
    return NightScore(len(days), sum(errors) / len(days), sum(right) / len(days))


def full_times(
    highest: Mapping[datetime, float], tz: tzinfo
) -> dict[date, datetime | None]:
    """When the battery first got full each local day; None for days it did not.

    *highest* is the highest level per period (5 minutes or an hour); the time
    is the start of the first period that reached full.
    """
    out: dict[date, datetime | None] = {}
    for start in sorted(highest):
        day = start.astimezone(tz).date()
        out.setdefault(day, None)
        if out[day] is None and highest[start] >= FULL_PCT:
            out[day] = start
    return out


@dataclass(frozen=True, slots=True)
class FullScore:
    days: int
    # Mean absolute error of the time, on days both said full.
    error_minutes: float | None
    # Days where one said full and the other did not.
    missed: int


def score_full(
    predicted: Mapping[date, datetime | None],
    actual: Mapping[date, datetime | None],
) -> FullScore:
    days = [d for d in sorted(predicted) if d in actual]
    both = [d for d in days if predicted[d] is not None and actual[d] is not None]
    missed = sum((predicted[d] is None) != (actual[d] is None) for d in days)
    minutes = [
        abs((predicted[d] - actual[d]).total_seconds()) / 60  # type: ignore[operator]
        for d in both
    ]
    return FullScore(
        days=len(days),
        error_minutes=sum(minutes) / len(minutes) if minutes else None,
        missed=missed,
    )
