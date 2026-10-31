"""Day facts from the recorder, and what a home can measure."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from custom_components.solar_energy_insights.core.achievements.facts import (
    DayFacts,
    Series,
    build,
)
from custom_components.solar_energy_insights.core.achievements.profile import (
    Capability,
    Season,
    profile_of,
    season_of,
)
from custom_components.solar_energy_insights.core.backtest import Evening
from tests.core.achievements.homes import home, without
from tests.core.helpers import TZ, at

DAY = date(2026, 6, 10)


def hours(value) -> dict:
    return {(DAY, h): value(h) for h in range(24)}


def test_a_day_is_reduced_to_its_facts() -> None:
    series = Series(
        house=hours(lambda _h: 0.5),
        solar=hours(lambda h: 2.0 if 9 <= h < 17 else 0.0),
        imported=hours(lambda h: 0.4 if h < 6 else 0.0),
        exported=hours(lambda h: 0.5 if 12 <= h < 15 else 0.0),
        soc_max=hours(lambda h: 100.0 if h >= 13 else 40.0),
        soc_min=hours(lambda _h: 20.0),
        solar_peak=hours(lambda h: 6.2 if h == 12 else 1.0),
    )
    evening = Evening(DAY, 90.0, at(DAY + timedelta(days=1), 5), 31.0)

    f = build(
        DAY,
        TZ,
        series,
        floor_pct=5,
        capacity_kwh=10,
        evening=evening,
        forecast_kwh=15.0,
        said={"full_at": at(DAY, 13).isoformat(), "outlook": "borderline"},
    )

    assert f is not None
    assert f.house_kwh == 12.0
    assert f.solar_kwh == 16.0
    assert f.import_kwh == pytest.approx(2.4)
    assert f.export_kwh == pytest.approx(1.5)
    assert f.offgrid_hours == 18
    assert f.full_at == datetime(2026, 6, 10, 13, tzinfo=TZ)
    assert f.night_lasted is True
    assert f.night_spare_kwh == pytest.approx(2.6)
    assert f.peak_kw == 6.2
    assert f.beat_forecast is True
    assert f.said_outlook == "borderline"
    assert f.said_full is True


def test_five_minute_readings_place_full_to_the_minute() -> None:
    fine = {at(DAY, 12, 0) + timedelta(minutes=5 * k): 95.0 + k for k in range(8)}
    series = Series(
        house=hours(lambda _h: 0.5),
        soc_max=hours(lambda _h: 100.0),
        soc_min=hours(lambda _h: 50.0),
        soc_fine=fine,
    )

    f = build(DAY, TZ, series)

    assert f is not None
    assert f.full_at == at(DAY, 12, 20)


def test_a_mostly_missing_day_is_no_day() -> None:
    series = Series(house={(DAY, h): 0.5 for h in range(10)})

    assert build(DAY, TZ, series) is None


def test_facts_survive_storage() -> None:
    f = home(3, DAY)[-1]

    assert DayFacts.from_dict(f.as_dict()) == f


def test_capabilities_come_from_what_was_measured() -> None:
    full = profile_of(home(30, DAY), 10.0, 48.4)
    bare = profile_of(
        home(
            30,
            DAY,
            tweak=without(
                "solar_kwh",
                "solar_best_hour_kwh",
                "import_kwh",
                "export_kwh",
                "forecast_kwh",
                "peak_kw",
            ),
        ),
        10.0,
        48.4,
    )

    assert {Capability.SOLAR, Capability.GRID, Capability.FORECAST} <= full.capabilities
    assert Capability.SOLAR not in bare.capabilities
    assert Capability.GRID not in bare.capabilities
    assert {Capability.BATTERY, Capability.HOUSE} <= bare.capabilities


@pytest.mark.parametrize("kwp", (4.0, 10.0, 15.0))
def test_the_arrays_size_is_seen_from_its_best_hours(kwp: float) -> None:
    profile = profile_of(home(60, DAY, kwp=kwp), 10.0, 48.4)

    assert profile.kwp == pytest.approx(kwp, rel=0.1)


def test_seasons_follow_the_hemisphere() -> None:
    december = date(2026, 12, 25)

    assert season_of(december) is Season.WINTER
    assert season_of(december, southern=True) is Season.SUMMER
    assert season_of(date(2026, 3, 21)) is Season.SPRING
    assert season_of(date(2026, 3, 21), southern=True) is Season.AUTUMN


def test_near_the_equator_there_are_no_seasonal_badges() -> None:
    assert Capability.SEASONS not in profile_of(home(30, DAY), 10.0, 1.3).capabilities
    assert Capability.SEASONS in profile_of(home(30, DAY), 10.0, -33.9).capabilities
