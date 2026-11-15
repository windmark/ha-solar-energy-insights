"""Reading Solcast's forecast."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.solar_energy_insights.core.forecast import PvForecast, Slot
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


def _morning(kws: list[float], first: datetime) -> PvForecast:
    return PvForecast(
        Slot(first + timedelta(minutes=30 * k), kw, kw, kw) for k, kw in enumerate(kws)
    )


def test_the_sun_reaches_a_level_between_half_hours() -> None:
    # Slots from 07:30, read at their midpoints: 0.8 kW at 08:45, 1.6 kW at 09:15
    first = datetime(2026, 11, 16, 7, 30, tzinfo=TZ)
    pv = _morning([0.0, 0.4, 0.8, 1.6, 2.0, 1.2, 0.3, 0.0], first)
    evening = datetime(2026, 11, 15, 20, 0, tzinfo=TZ)

    assert pv.first_reach(1.0, evening) == datetime(2026, 11, 16, 8, 52, 30, tzinfo=TZ)
    assert pv.first_reach(5.0, evening) is None


def test_while_the_sun_is_up_it_is_the_next_mornings_rise() -> None:
    day = datetime(2026, 11, 16, 7, 30, tzinfo=TZ)
    pv = PvForecast(
        [
            *_morning([0.0, 2.0, 2.0, 0.0], day).slots,
            *_morning([0.0, 0.5, 1.5, 0.0], day + timedelta(days=1)).slots,
        ]
    )

    reached = pv.first_reach(1.0, day + timedelta(minutes=50))
    assert reached is not None
    assert reached.date() == date(2026, 11, 17)
