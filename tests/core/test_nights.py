"""Past days stand in for the hours ahead."""

from __future__ import annotations

from datetime import date, timedelta

from custom_components.solar_energy_insights.core.nights import (
    hourly_use,
    past_days,
    typical,
)
from tests.core.helpers import TZ, at

DAY = date(2026, 10, 20)


def full_days(values: dict[int, float], *, missing: tuple[int, int] | None = None):
    """Whole recorded days: days_ago → kWh every hour, optionally one hour missing."""
    return {
        (DAY - timedelta(days=n), hour): kwh
        for n, kwh in values.items()
        for hour in range(24)
        if (n, hour) != missing
    }


def test_a_past_day_replays_its_own_hours() -> None:
    use = full_days({1: 2.0, 2: 0.5})

    yesterday, before = past_days(use, TZ, 2, today=DAY)

    assert yesterday.rate(at(DAY, 19, 30)) == 2.0
    assert before.rate(at(DAY, 19, 30)) == 0.5


def test_a_missing_hour_takes_the_usual_value_not_zero() -> None:
    use = full_days({1: 0.4, 2: 0.4, 3: 0.4}, missing=(1, 3))

    yesterday = past_days(use, TZ, 1, today=DAY)[0]

    assert yesterday.rate(at(DAY, 3)) == 0.4


def test_glitches_are_left_out() -> None:
    rows = {at(DAY, 10): 0.5, at(DAY, 11): 999.0, at(DAY, 12): -3.0}

    use = hourly_use(rows, TZ)

    assert list(use.values()) == [0.5]


def test_the_autumn_hour_seen_twice_holds_both() -> None:
    autumn = date(2026, 10, 25)
    first = at(autumn, 1) + timedelta(hours=1)
    rows = {first: 0.3, first + timedelta(hours=1): 0.2}

    use = hourly_use(rows, TZ)

    assert sum(use.values()) == 0.5
    assert len(use) == 1


def test_typical_is_the_median_of_each_hour() -> None:
    use = {
        (DAY - timedelta(days=n), 8): kwh for n, kwh in ((1, 0.2), (2, 0.4), (3, 3.0))
    }

    assert typical(use, TZ)(at(DAY, 8)) == 0.4
