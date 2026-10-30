"""Reading Solcast's forecast."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.solar_energy_insights.core.forecast import PvForecast
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
