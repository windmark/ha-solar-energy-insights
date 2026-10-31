"""The plan's own answers, scored against what happened."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.solar_energy_insights.core.live import (
    NightAnswer,
    full_times,
    score_full,
    score_nights,
)
from tests.core.helpers import TZ, at

DAY = date(2026, 6, 10)


def test_night_answers_are_scored_only_when_they_could_have_gone_either_way() -> None:
    days = [DAY - timedelta(days=n) for n in range(4)]
    answers = {
        days[0]: NightAnswer(1.0, True),  # said lasts, did: 0.5 off
        days[1]: NightAnswer(0.0, False),  # said grid, it was: nothing to learn
        days[2]: NightAnswer(0.8, True),  # said lasts, ran out: wrong
        days[3]: NightAnswer(2.0, True),  # no record of what happened
    }
    spare = {days[0]: 1.5, days[1]: 0.0, days[2]: 0.0}
    lasted = {days[0]: True, days[1]: False, days[2]: False}

    result = score_nights(answers, spare, lasted)

    assert result.nights == 2
    assert result.error_kwh == pytest.approx((0.5 + 0.8) / 2)
    assert result.lasts_right == pytest.approx(0.5)


def test_full_is_the_first_period_that_reached_it() -> None:
    highest = {at(DAY, h): v for h, v in ((10, 80.0), (11, 99.5), (12, 100.0))}
    highest |= {at(DAY + timedelta(days=1), h): 70.0 for h in range(9, 17)}

    times = full_times(highest, TZ)

    assert times[DAY] == at(DAY, 11)
    assert times[DAY + timedelta(days=1)] is None


def test_full_at_is_scored_in_minutes_and_misses_are_counted() -> None:
    days = [DAY - timedelta(days=n) for n in range(3)]
    predicted = {days[0]: at(days[0], 11), days[1]: at(days[1], 12), days[2]: None}
    actual = {days[0]: at(days[0], 11, 30), days[1]: None, days[2]: None}

    result = score_full(predicted, actual)

    assert result.days == 3
    assert result.error_minutes == pytest.approx(30)
    assert result.missed == 1
