"""A correction to Solcast for this roof: learned, judged on left-out days,
used only when it helps."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from custom_components.solar_energy_insights.core.correction import (
    LIMITS,
    MIN_DAYS,
    learn,
)
from tests.core.helpers import TZ, sunny

DAY = date(2026, 6, 10)


def days(count: int, made, wobble: float = 0.1):
    """Forecasts and what the panels made, per day, as 24 hourly kWh."""
    forecasts, actual = {}, {}
    for n in range(count):
        day = DAY - timedelta(days=n + 1)
        hourly = np.array(sunny(day, peak_kw=6).hourly(day, TZ))
        noise = 1 + wobble * np.sin(n * 1.7 + np.arange(24))
        forecasts[day] = hourly
        actual[day] = made(hourly) * noise
    return forecasts, actual


def test_a_steady_shortfall_is_learned_and_used() -> None:
    forecasts, actual = days(30, lambda f: f * 0.8)

    correction = learn(forecasts, actual)

    assert correction.active
    assert correction.corrected_error_kwh is not None
    assert correction.raw_error_kwh is not None
    assert correction.corrected_error_kwh < correction.raw_error_kwh
    midday = correction.factors[11:15]
    assert np.all((midday > 0.7) & (midday < 0.9))


def test_a_forecast_that_is_already_right_is_left_alone() -> None:
    forecasts, actual = days(30, lambda f: f)

    assert not learn(forecasts, actual).active


def test_too_few_days_teach_nothing() -> None:
    forecasts, actual = days(MIN_DAYS - 1, lambda f: f * 0.5)

    correction = learn(forecasts, actual)

    assert not correction.active
    assert correction.days == MIN_DAYS - 1


def test_the_correction_stays_bounded() -> None:
    forecasts, actual = days(30, lambda f: f * 5, wobble=0)

    correction = learn(forecasts, actual)

    assert np.all(correction.factors >= LIMITS[0])
    assert np.all(correction.factors <= LIMITS[1])


@pytest.mark.parametrize("active", (True, False))
def test_the_forecast_changes_only_when_the_correction_is_used(active: bool) -> None:
    forecasts, actual = days(30, lambda f: f * 0.8 if active else f)
    correction = learn(forecasts, actual)
    forecast = sunny(DAY, peak_kw=6)

    corrected = correction.apply(forecast, TZ)

    assert (corrected == forecast) is not active
