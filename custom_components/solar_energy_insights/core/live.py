"""Score the plan's own answers, saved at the time, against what happened.

The backtest replays what the plan would have said; this checks what it did
say. The night answer is saved at 21:00, "full at" in the morning.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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


@dataclass(frozen=True, slots=True)
class Call:
    """One night: what the plan said at 21:00, and what happened."""

    day: date
    said_spare_kwh: float
    said_lasts: bool
    spare_kwh: float
    lasted: bool

    @property
    def contested(self) -> bool:
        """Could have gone either way: the call or the night said it lasts."""
        return self.said_lasts or self.lasted

    @property
    def right(self) -> bool:
        return self.said_lasts == self.lasted


def calls(
    answers: Mapping[date, NightAnswer],
    actual_spare_kwh: Mapping[date, float],
    actual_lasts: Mapping[date, bool],
) -> list[Call]:
    """Every night with both a 21:00 answer and a known outcome, oldest first.
    The one record both the live score and "last night" are read from."""
    return [
        Call(
            d,
            answers[d].spare_kwh,
            answers[d].lasts,
            actual_spare_kwh[d],
            actual_lasts[d],
        )
        for d in sorted(answers)
        if d in actual_spare_kwh and d in actual_lasts
    ]


def score_nights(nights: Sequence[Call]) -> NightScore:
    """On nights that could have gone either way, as in the backtest."""
    contested = [c for c in nights if c.contested]
    if not contested:
        return NightScore(0, None, None)
    errors = [abs(c.said_spare_kwh - c.spare_kwh) for c in contested]
    right = [c.right for c in contested]
    return NightScore(
        len(contested), sum(errors) / len(contested), sum(right) / len(contested)
    )


def full_times(
    highest: Mapping[datetime, float], tz: tzinfo, full_pct: float = FULL_PCT
) -> dict[date, datetime | None]:
    """When the battery first got full each local day; None for days it did not.

    *highest* is the highest level per period (5 minutes or an hour); the time
    is the start of the first period that reached *full_pct*, which is just
    below the battery's charge limit.
    """
    out: dict[date, datetime | None] = {}
    for start in sorted(highest):
        day = start.astimezone(tz).date()
        out.setdefault(day, None)
        if out[day] is None and highest[start] >= full_pct:
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
