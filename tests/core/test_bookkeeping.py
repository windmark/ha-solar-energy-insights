"""Day records: the score, the sizing verdict and the flags streaks build on."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from custom_components.solar_energy_insights.core.bookkeeping import (
    DayRecord,
    DayTotals,
    Forecasts,
    Night,
    Verdict,
    score,
    sizing_verdict,
)

GOOD = DayTotals(solar=40, house=12, imp=0.5, exp=20, charged=10, discharged=9)
POOR = DayTotals(solar=5, house=14, imp=10, exp=1, charged=2, discharged=1)


def test_scores_stay_between_0_and_100() -> None:
    for totals in (GOOD, POOR, DayTotals(), DayTotals(solar=0, house=0, imp=0, exp=0)):
        result = score(totals, capacity_kwh=15, floor_pct=10, forecast_kwh=30)
        assert 0 <= result.total <= 100
        assert all(0 <= part <= 1 for part in result.parts.values())


def test_a_better_day_scores_higher() -> None:
    good = score(GOOD, capacity_kwh=15, floor_pct=10, forecast_kwh=35)
    poor = score(POOR, capacity_kwh=15, floor_pct=10, forecast_kwh=35)

    assert good.total > poor.total


@pytest.mark.parametrize(
    ("soc_max", "min_after_full", "expected"),
    (
        (80.0, None, Verdict.NEVER_FULL),
        (100.0, 11.0, Verdict.TOO_SMALL),
        (100.0, 40.0, Verdict.RIGHT),
        (None, None, None),
    ),
)
def test_sizing_verdict(soc_max, min_after_full, expected) -> None:
    assert sizing_verdict(soc_max, min_after_full, floor_pct=10) == expected


def test_a_record_survives_a_round_trip_with_its_flags() -> None:
    record = DayRecord(
        day=date(2026, 6, 10),
        totals=GOOD,
        soc_max=100.0,
        full_at=datetime(2026, 6, 10, 11, tzinfo=UTC),
        peak_kw=9.1,
        forecasts=Forecasts(morning=35.0, morning_hourly=[0.0] * 24),
        score=score(GOOD, 15, 10, 35),
        night=Night(
            grid_kwh=0.0,
            min_soc_after_full=30,
            export_after_full_kwh=12,
            verdict=Verdict.RIGHT,
        ),
    )

    restored = DayRecord.from_dict(record.as_dict())

    assert restored == record
    assert restored.flags == {
        "self_sufficient": True,
        "battery_full": True,
        "beat_forecast": True,
        "grid_free_night": True,
    }


def test_unknown_facts_stay_unknown() -> None:
    flags = DayRecord(day=date(2026, 6, 10), totals=DayTotals()).flags

    assert set(flags.values()) == {None}
