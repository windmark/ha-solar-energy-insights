"""The last year again, with a bigger battery, a heat pump or one string fewer."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from custom_components.solar_energy_insights.core import batch
from custom_components.solar_energy_insights.core.batch import Slices
from custom_components.solar_energy_insights.core.battery import Battery
from custom_components.solar_energy_insights.core.scenario import (
    HeatPump,
    Hour,
    Scenario,
    compare,
    replay,
    string_worth,
)

START = datetime(2025, 11, 1, tzinfo=UTC)
BATTERY = Battery(capacity_kwh=10.0, soc_pct=50.0, floor_pct=10.0)
PRICE, FEED_IN = 0.30, 0.08


def year(days: int = 365) -> list[Hour]:
    """A year in Augsburg, roughly: long bright days in summer, short dull ones
    in winter, an evening peak in the house's use every day."""
    out = []
    for n in range(days):
        season = math.cos(2 * math.pi * (n - 233) / 365)  # 1 in late June
        summer = (season + 1) / 2
        peak_kw = 0.4 + 9.6 * summer**1.5
        rise, length = 8 - 3 * summer, 8 + 8 * summer
        mean_c = 9.0 + 10.0 * season
        for hour in range(24):
            light = math.sin(math.pi * (hour + 0.5 - rise) / length)
            house = 0.9 if 17 <= hour < 22 else 0.35
            out.append(
                Hour(
                    START + timedelta(days=n, hours=hour),
                    peak_kw * light if rise <= hour + 0.5 < rise + length else 0.0,
                    house,
                    mean_c + 4.0 * math.sin(math.pi * (hour - 9) / 12),
                )
            )
    return out


def test_import_and_export_add_up() -> None:
    hours = year(60)
    outcome = replay(hours, BATTERY)

    sun = np.array([h.solar_kwh for h in hours])
    load = np.array([h.house_kwh for h in hours])
    runs = batch.run(
        BATTERY,
        [BATTERY.soc_pct],
        Slices(
            tuple(h.start for h in hours),
            np.ones(len(hours)),
            np.ones(len(hours), dtype=bool),
        ),
        sun,
        load[np.newaxis, :],
    )
    # The engine's own count of the grid agrees with the one from the levels.
    assert outcome.import_kwh == pytest.approx(float(runs.grid_kwh[0]), rel=1e-6)
    # The house's own power came from the roof or from what the battery held
    # at the start; the rest of what the roof kept was lost or is stored.
    kept = outcome.solar_kwh - outcome.export_kwh
    assert kept + BATTERY.stored_kwh >= outcome.load_kwh - outcome.import_kwh


def test_a_bigger_battery_buys_less_and_lasts_more_nights() -> None:
    hours = year()
    result = compare(hours, BATTERY, Scenario(extra_battery_kwh=5.0), PRICE, FEED_IN)

    assert result.changed.import_kwh < result.now.import_kwh
    assert result.changed.export_kwh < result.now.export_kwh
    assert result.changed.nights_off_grid >= result.now.nights_off_grid
    assert result.changed.self_sufficiency > result.now.self_sufficiency
    assert result.saving_per_year > 0


def test_more_battery_never_helps_more_each_time() -> None:
    hours = year()
    savings = [
        compare(
            hours, BATTERY, Scenario(extra_battery_kwh=e), PRICE, FEED_IN
        ).saving_per_year
        for e in (0.0, 5.0, 10.0, 15.0)
    ]

    assert savings[0] == pytest.approx(0.0)
    steps = np.diff(savings)
    assert all(steps > 0)
    assert all(np.diff(steps) <= 1e-6)


def test_a_heat_pump_needs_most_in_winter_and_only_part_comes_from_the_roof() -> None:
    hours = year()
    pump = HeatPump(heat_loss_w_per_k=180.0, hot_water_kwh_per_day=6.0)
    result = compare(hours, BATTERY, Scenario(heat_pump=pump), PRICE, FEED_IN)

    assert result.changed.heat_pump_kwh > 1500
    assert result.saving_per_year < 0
    share = result.heat_pump_solar_share
    assert share is not None
    assert 0.05 < share < 0.6


def test_a_house_losing_less_heat_needs_less() -> None:
    hours = year(90)
    leaky = replay(hours, BATTERY, HeatPump(heat_loss_w_per_k=300.0))
    tight = replay(hours, BATTERY, HeatPump(heat_loss_w_per_k=120.0))

    assert tight.heat_pump_kwh < leaky.heat_pump_kwh
    assert tight.import_kwh < leaky.import_kwh


def test_no_heating_above_the_heating_limit() -> None:
    pump = HeatPump(heat_loss_w_per_k=200.0, heating_limit_c=15.0)

    assert pump.kwh(16.0) == 0.0
    assert pump.kwh(0.0) > pump.kwh(10.0) > 0
    # Colder air: both more heat and a worse coefficient of performance.
    assert pump.cop(-7.0) < pump.cop(7.0)


def test_a_heat_pump_without_temperatures_is_refused() -> None:
    hours = [Hour(h.start, h.solar_kwh, h.house_kwh) for h in year(3)]

    with pytest.raises(ValueError, match="outdoor temperature"):
        replay(hours, BATTERY, HeatPump(heat_loss_w_per_k=150.0))


def test_part_of_a_year_is_scaled_to_a_year() -> None:
    hours = year(91)
    result = compare(hours, BATTERY, Scenario(extra_battery_kwh=5.0), PRICE, FEED_IN)

    assert result.now.days == pytest.approx(91)
    assert result.per_year == pytest.approx(365 / 91)


def test_a_battery_that_lasts_every_night_gains_nothing_from_more() -> None:
    # Sunny every day, a small house: the battery lasts every night already.
    hours = [
        Hour(START + timedelta(hours=k), 5.0 if 8 <= k % 24 < 16 else 0.0, 0.3)
        for k in range(30 * 24)
    ]
    result = compare(hours, BATTERY, Scenario(extra_battery_kwh=5.0), PRICE, FEED_IN)

    assert result.now.nights_off_grid == result.now.nights
    assert result.saving_per_year == pytest.approx(0.0, abs=0.5)


def test_no_hours_no_answer() -> None:
    result = compare([], BATTERY, Scenario(extra_battery_kwh=5.0), PRICE, FEED_IN)

    assert result.now.hours == 0
    assert result.saving_per_year == 0.0


def with_strings(hours: list[Hour], north_morning: float = 0.5) -> list[Hour]:
    """Three strings: two south ones even through the day, and a north one
    that makes a bigger share early and late than at noon."""
    out = []
    for h in hours:
        local = h.start.hour
        edge = 1.0 if local < 10 or local >= 15 else 0.0
        north = 0.15 + north_morning * edge
        south = (1 - north) / 2
        sun = h.solar_kwh
        out.append(
            replace(
                h,
                strings=((1, sun * south), (2, sun * south), (3, sun * north))
                if sun > 0
                else ((1, 0.0), (2, 0.0), (3, 0.0)),
                metered_import_kwh=0.2,
                metered_export_kwh=0.1,
            )
        )
    return out


def test_without_a_string_the_house_buys_more_and_sells_less() -> None:
    hours = with_strings(year(90))

    worth = string_worth(hours, BATTERY, 3, PRICE, FEED_IN)
    c = worth.comparison

    assert worth.made_kwh == pytest.approx(c.now.solar_kwh - c.changed.solar_kwh)
    assert c.changed.import_kwh > c.now.import_kwh
    assert c.changed.export_kwh <= c.now.export_kwh
    assert worth.value > 0
    # Never worth more than all it made at the price of buying it.
    assert worth.value <= worth.made_kwh * PRICE + 1e-6


def test_a_string_that_makes_nothing_is_worth_nothing() -> None:
    hours = [
        replace(h, strings=((1, h.solar_kwh), (2, 0.0), (3, 0.0))) for h in year(30)
    ]

    worth = string_worth(hours, BATTERY, 3, PRICE, FEED_IN)

    assert worth.made_kwh == 0
    assert worth.value == pytest.approx(0.0, abs=1e-9)


def test_hours_without_string_readings_are_left_out() -> None:
    known = with_strings(year(20))
    before = year(10)

    worth = string_worth(before + known, BATTERY, 3, PRICE, FEED_IN)

    assert worth.comparison.now.hours == len(known)
    assert worth.metered_hours == len(known)
    assert worth.metered_import_kwh == pytest.approx(0.2 * len(known))


def test_the_grid_takes_no_more_than_the_cap() -> None:
    hours = year(60)

    free = replay(hours, BATTERY)
    capped = replay(hours, BATTERY, export_cap_kw=1.0)

    assert capped.export_kwh < free.export_kwh
    assert capped.import_kwh == pytest.approx(free.import_kwh)
