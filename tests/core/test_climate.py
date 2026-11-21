"""A whole year from part of one: the missing days borrowed from recorded ones."""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pytest

from custom_components.solar_energy_insights.core.battery import Battery
from custom_components.solar_energy_insights.core.climate import (
    COLDEST_DAY_NORTH,
    daily_extraterrestrial,
    declination_deg,
    fill_year,
    fit_annual_cycle,
)
from custom_components.solar_energy_insights.core.scenario import (
    Comparison,
    HeatPump,
    Scenario,
    compare,
)
from tests.core.weather_year import (
    AMPLITUDE_C,
    END,
    LATITUDE,
    MEAN_C,
    weather_year,
)

BATTERY = Battery(capacity_kwh=10.0, soc_pct=10.0, floor_pct=10.0)
RENOVATED = HeatPump(150.0, heating_limit_c=12.5, hot_water_kwh_per_day=8.2)


def test_the_declination_follows_the_solstices_and_equinoxes() -> None:
    assert declination_deg(172) == pytest.approx(23.45, abs=0.1)  # 21 June
    assert declination_deg(355) == pytest.approx(-23.45, abs=0.1)  # 21 December
    assert declination_deg(81) == pytest.approx(0.0, abs=0.5)  # 22 March


def test_sunlight_above_the_atmosphere_matches_duffie_and_beckman() -> None:
    # Their example 1.10.1: 43° N on 15 April gets 33.8 MJ/m² (G_sc 1367 W/m²).
    mj = daily_extraterrestrial(105, 43.0) * 24 * 3600 * 1367 / math.pi / 1e6

    assert mj == pytest.approx(33.8, rel=0.01)
    # Augsburg gets about five times as much at midsummer as at midwinter.
    ratio = daily_extraterrestrial(172, LATITUDE) / daily_extraterrestrial(
        355, LATITUDE
    )
    assert 4.5 < ratio < 5.5
    # Polar night: nothing, and no error.
    assert daily_extraterrestrial(355, 80.0) == 0.0


def test_the_seasons_temperature_is_found_from_half_a_year() -> None:
    # The warm half only, from May to November, with no spells.
    days = np.arange(127, 325)
    means = MEAN_C - AMPLITUDE_C * np.cos(2 * np.pi * (days - 17) / 365.25)

    cycle = fit_annual_cycle(days, means, LATITUDE)

    assert cycle is not None
    assert cycle.mean_c == pytest.approx(MEAN_C, abs=0.3)
    assert cycle.amplitude_c == pytest.approx(AMPLITUDE_C, abs=0.3)
    assert cycle.coldest_day == COLDEST_DAY_NORTH


def test_with_most_of_a_year_the_phase_is_fitted_too() -> None:
    days = np.arange(1, 366)
    means = 10.0 - 9.0 * np.cos(2 * np.pi * (days - 30) / 365.25)

    cycle = fit_annual_cycle(days, means, LATITUDE)

    assert cycle is not None
    assert cycle.coldest_day == pytest.approx(30, abs=0.5)
    assert cycle.amplitude_c == pytest.approx(9.0, abs=0.05)


def test_a_few_summer_weeks_cannot_tell_the_season() -> None:
    days = np.arange(170, 235)
    means = 18.0 + np.zeros(len(days))

    assert fit_annual_cycle(days, means, LATITUDE) is None


def test_a_whole_year_is_left_as_it_was() -> None:
    hours = weather_year()

    year = fill_year(hours, END, LATITUDE)

    assert year.filled
    assert year.filled_hours == 0
    assert year.hours == hours


def test_a_half_year_is_filled_to_a_whole_one() -> None:
    hours = weather_year()
    part = [h for h in hours if h.start >= END - timedelta(days=197)]

    year = fill_year(part, END, LATITUDE)

    assert year.filled
    assert len(year.hours) == 365 * 24
    assert year.recorded_hours == len(part)
    assert year.filled_hours == 365 * 24 - len(part)
    starts = [h.start for h in year.hours]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)
    # January was borrowed whole, the recorded months kept.
    assert year.filled_share_by_month[0] == 1.0
    assert year.filled_share_by_month[7] == 0.0
    assert all(
        not h.measured for h in year.hours if h.start not in {p.start for p in part}
    )


def test_the_filled_winter_is_cold_and_dark() -> None:
    hours = weather_year()
    part = [h for h in hours if h.start >= END - timedelta(days=197)]

    year = fill_year(part, END, LATITUDE)

    def mean_in(month: int, of: str) -> float:
        rows = [getattr(h, of) for h in year.hours if h.start.month == month]
        return float(np.mean(rows))

    true_january = np.mean([h.outdoor_c for h in hours if h.start.month == 1])
    assert mean_in(1, "outdoor_c") == pytest.approx(true_january, abs=2.5)
    assert mean_in(1, "outdoor_c") < mean_in(10, "outdoor_c") - 5
    assert mean_in(12, "solar_kwh") < mean_in(10, "solar_kwh")
    assert mean_in(6, "solar_kwh") > 2 * mean_in(12, "solar_kwh")


def test_the_same_hours_fill_the_same_year() -> None:
    part = [h for h in weather_year() if h.start >= END - timedelta(days=120)]

    assert fill_year(part, END, LATITUDE).hours == fill_year(part, END, LATITUDE).hours


def test_too_few_days_are_not_filled() -> None:
    part = [h for h in weather_year() if h.start >= END - timedelta(days=20)]

    year = fill_year(part, END, LATITUDE)

    assert not year.filled
    assert year.hours == part


def test_a_filled_half_year_answers_much_like_the_whole_year() -> None:
    """The point of filling: half a year scaled up takes the summer for the
    winter, so the heat pump looks small and sunny. Filled, it comes close."""
    hours = weather_year()
    part = [h for h in hours if h.start >= END - timedelta(days=197)]
    scenario = Scenario(heat_pump=RENOVATED)

    whole = compare(hours, BATTERY, scenario, 0.30, 0.08)
    scaled = compare(part, BATTERY, scenario, 0.30, 0.08)
    filled = compare(fill_year(part, END, LATITUDE).hours, BATTERY, scenario, 0.3, 0.08)

    def heat(c: Comparison) -> float:  # per year
        return c.changed.space_heat_kwh * c.per_year

    def roof(c: Comparison) -> float:
        assert c.heat_pump_solar_share is not None
        return c.heat_pump_solar_share

    assert heat(scaled) < 0.5 * heat(whole)
    assert heat(filled) == pytest.approx(heat(whole), rel=0.15)
    assert roof(scaled) > 1.5 * roof(whole)
    assert roof(filled) == pytest.approx(roof(whole), abs=0.05)
