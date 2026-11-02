"""Frozen forecasts at the moments decisions are made, and how far they held."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from custom_components.solar_energy_insights.core.checkpoints import (
    Judged,
    Snapshot,
    group_of,
    judge,
    score,
    scores,
)
from tests.core.helpers import TZ

TODAY = date(2026, 11, 3)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def snap(name: str, day: date, kw: float = 2.0) -> Snapshot:
    """A forecast of *kw* likely (low half, high one and a half) from 08 to 16."""
    slots = tuple(
        (at(day, 8) + timedelta(minutes=30 * k), kw, kw / 2, kw * 1.5)
        for k in range(16)
    )
    return Snapshot(name, day, at(day, 6), at(day, 5, 40), slots)


def day_of(
    name: str, back: int, forecast: float, actual: float, group: str = "mixed"
) -> Judged:
    return Judged(
        name,
        TODAY - timedelta(days=back),
        forecast,
        forecast * 0.5,
        forecast * 1.5,
        actual,
        group,
    )


def test_a_snapshot_survives_storage() -> None:
    s = snap("06:00", TODAY)

    assert Snapshot.from_dict(s.as_dict()) == s


def test_the_noon_checkpoint_is_judged_on_the_afternoon_only() -> None:
    s = snap("12:00", TODAY)

    whole = judge(s, actual_kwh=14.0, clear_kwh=30.0)
    afternoon = judge(s, actual_kwh=6.0, clear_kwh=30.0, noon=at(TODAY, 12))

    assert whole.forecast_kwh == pytest.approx(16.0)
    assert afternoon.forecast_kwh == pytest.approx(8.0)
    assert afternoon.error_kwh == pytest.approx(2.0)
    assert afternoon.in_range


@pytest.mark.parametrize(
    ("forecast", "group"), ((25.0, "sunny"), (15.0, "mixed"), (5.0, "dull"))
)
def test_what_the_forecast_said_against_a_clear_day(
    forecast: float, group: str
) -> None:
    assert group_of(forecast, clear_kwh=30.0) == group


def test_no_score_until_there_are_enough_days() -> None:
    few = [day_of("06:00", k, 20.0, 18.0) for k in range(1, 6)]

    assert score(few).typical_kwh is None
    assert score(few).days == 5


def test_a_score_is_the_typical_miss_the_bias_and_the_ranges_hit_rate() -> None:
    days = [day_of("06:00", k, 20.0, 18.0) for k in range(1, 11)] + [
        day_of("06:00", 11, 20.0, 35.0)
    ]

    s = score(days)

    assert s.typical_kwh == pytest.approx(2.0)
    assert s.bias_kwh == pytest.approx((10 * 2.0 - 15.0) / 11, abs=0.01)
    assert s.in_range == pytest.approx(10 / 11, abs=0.01)


def test_sunny_days_are_scored_apart_from_mixed_ones() -> None:
    sunny = [day_of("06:00", k, 30.0, 29.0, "sunny") for k in range(1, 10)]
    mixed = [day_of("06:00", k, 15.0, 9.0, "mixed") for k in range(10, 19)]

    s = scores(sunny + mixed, TODAY)["06:00"]

    assert s["groups"]["sunny"]["typical_kwh"] == pytest.approx(1.0)
    assert s["groups"]["mixed"]["typical_kwh"] == pytest.approx(6.0)
    assert s["groups"]["dull"]["typical_kwh"] is None


def test_the_same_weeks_last_year_are_set_beside_the_window() -> None:
    now = [day_of("06:00", k, 20.0, 19.0) for k in range(1, 12)]
    year = [day_of("06:00", 365 + k, 20.0, 15.0) for k in range(-12, 0)]

    s = scores(now + year, TODAY)["06:00"]

    assert s["typical_kwh"] == pytest.approx(1.0)
    assert s["last_year"]["typical_kwh"] == pytest.approx(5.0)


def test_waiting_for_noon_pays_when_it_cuts_the_miss() -> None:
    early = [day_of("06:00@noon", k, 10.0, 6.0) for k in range(1, 12)]
    late = [day_of("12:00", k, 7.0, 6.0) for k in range(1, 12)]
    evening = [day_of("18:00", k, 20.0, 15.0) for k in range(1, 12)]
    later = [
        day_of("21:00", k, 19.5, 15.0) for k in range(1, 12)
    ]  # 10 % better: not enough

    waiting = scores(early + late + evening + later, TODAY)["waiting"]

    assert waiting["12:00 vs 06:00"] == {
        "days": 11,
        "early_kwh": 4.0,
        "late_kwh": 1.0,
        "pays": True,
    }
    assert waiting["21:00 vs 18:00"]["pays"] is False
