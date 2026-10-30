"""Reading Solcast, and the correction that has to earn its place."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.solar_energy_insights.core.forecast import PvBias, PvForecast
from tests.core.helpers import TZ, at, sunny

DAY = date(2026, 6, 10)


def test_reads_solcast_periods_as_strings_or_datetimes() -> None:
    start = at(DAY, 12)
    forecast = PvForecast.from_solcast(
        [{"period_start": start.isoformat(), "pv_estimate": 4.0}],
        [{"period_start": start + timedelta(minutes=30), "pv_estimate": 2.0}],
    )

    assert forecast.kw_at(start + timedelta(minutes=10)) == 4.0
    assert forecast.kw_at(start + timedelta(minutes=40)) == 2.0
    assert forecast.energy(start, start + timedelta(hours=1)) == pytest.approx(3.0)


def test_skips_what_it_cannot_read() -> None:
    forecast = PvForecast.from_solcast(
        [
            {"period_start": "not a time", "pv_estimate": 1},
            {"period_start": "2026-06-10T12:00:00", "pv_estimate": 1},  # no timezone
            {"pv_estimate": 1},
        ],
        None,
    )

    assert len(forecast) == 0
    assert forecast.kw_at(datetime(2026, 6, 10, 12, tzinfo=UTC)) == 0.0


def test_hourly_totals_match_the_day() -> None:
    forecast = sunny(DAY)

    hourly = forecast.hourly(DAY, TZ)

    assert sum(hourly) == pytest.approx(forecast.energy(at(DAY, 0), at(DAY, 23, 59)))


def test_the_correction_waits_until_it_beats_the_raw_forecast() -> None:
    bias = PvBias.empty()
    forecast = sunny(DAY).hourly(DAY, TZ)
    # The roof makes 20 % less than forecast, every day.
    actual = [value * 0.8 for value in forecast]

    for _ in range(5):
        bias.observe_day(6, forecast, actual)
    assert not bias.active

    for _ in range(30):
        bias.observe_day(6, forecast, actual)
    assert bias.active
    assert (bias.improvement_pct or 0) > 0

    corrected = bias.apply(sunny(DAY), TZ)
    assert corrected.energy(at(DAY, 0), at(DAY, 23)) < sunny(DAY).energy(
        at(DAY, 0), at(DAY, 23)
    )


def test_the_correction_stays_off_when_the_forecast_is_already_right() -> None:
    bias = PvBias.empty()
    forecast = sunny(DAY).hourly(DAY, TZ)

    for _ in range(40):
        bias.observe_day(6, forecast, list(forecast))

    assert not bias.active


def test_the_correction_is_bounded_and_survives_a_round_trip() -> None:
    bias = PvBias.empty()
    forecast = sunny(DAY).hourly(DAY, TZ)
    for _ in range(60):
        bias.observe_day(6, forecast, [value * 5 for value in forecast])

    restored = PvBias.from_dict(bias.as_dict())

    assert all(0.5 <= restored.factor(6, hour) <= 1.5 for hour in range(24))
    assert restored.active == bias.active
