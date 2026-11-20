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
    COP_IN_THE_FIELD,
    G20_15,
    Gas,
    HeatPump,
    Hour,
    Scenario,
    compare,
    cop,
    degree_hours,
    replay,
    string_worth,
)
from tests.core.weather_year import weather_year

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


def test_no_heating_on_a_day_warmer_than_the_heating_limit() -> None:
    # A mild day, 16 °C on average, with a chilly morning.
    day = [
        Hour(START + timedelta(hours=k), 0.0, 0.3, 16.0 + (6 if k > 6 else -4))
        for k in range(24)
    ]
    cold = [replace(h, start=h.start + timedelta(days=1), outdoor_c=5.0) for h in day]
    pump = HeatPump(heat_loss_w_per_k=200.0, heating_limit_c=15.0)

    load = pump.load(
        [h.start for h in day + cold],
        np.array([h.outdoor_c for h in day + cold]),
        np.zeros(48),
    )

    assert load.space_heat[:24].sum() == 0.0
    assert load.space_heat[24:].sum() > 0
    assert load.heating_days == 1


def test_the_cop_is_the_published_curve_in_the_field() -> None:
    # Ruhnau et al. 2019, eq. 4, at a 33 K lift (A2/W35): 3.65, times 0.85.
    assert float(cop(33.0)) == pytest.approx(3.6545 * COP_IN_THE_FIELD, rel=1e-4)
    assert float(cop(33.0, rated_a2w35=4.6)) == pytest.approx(4.6 * COP_IN_THE_FIELD)
    lifts = np.linspace(15, 65, 11)
    assert all(np.diff(cop(lifts)) < 0)
    # Held to the curve's range, it never turns back up.
    assert float(cop(90.0)) == float(cop(65.0))


def test_the_heating_curve_runs_from_the_room_to_the_design_flow() -> None:
    pump = HeatPump(150.0, design_flow_c=45.0, design_outdoor_c=-12.0)

    assert float(pump.flow_c(-12.0)) == pytest.approx(45.0)
    assert float(pump.flow_c(20.0)) == pytest.approx(20.0)
    # Radiators give more than linearly with their excess temperature, so
    # half the load needs more than half the excess (EN 442, n = 1.3).
    assert float(pump.flow_c(4.0)) > 20.0 + 0.5 * 25
    # Colder air: both more heat and a worse coefficient of performance.
    assert float(pump.space_cop(-7.0)) < float(pump.space_cop(7.0))
    # A lower flow temperature is more efficient.
    assert float(HeatPump(150.0, design_flow_c=35.0).space_cop(0.0)) > float(
        pump.space_cop(0.0)
    )


def test_the_heat_is_the_heat_loss_times_the_degree_hours() -> None:
    hours = weather_year()
    pump = HeatPump(180.0, heating_limit_c=14.0)
    starts = [h.start for h in hours]
    temps = np.array([h.outdoor_c for h in hours])

    outcome = replay(hours, BATTERY, pump)

    assert outcome.space_heat_kwh == pytest.approx(
        180.0 * degree_hours(starts, temps, 20.0, 14.0) / 1000
    )


def test_degree_hours_count_as_germanys_degree_days_do() -> None:
    # A month at a steady 5 °C: 30 days of 15 K each, below the 15 °C limit.
    starts = [START + timedelta(hours=k) for k in range(30 * 24)]
    steady = np.full(len(starts), 5.0)

    assert degree_hours(starts, steady, *G20_15) == pytest.approx(30 * 15 * 24)
    assert degree_hours(starts, steady + 11.0, *G20_15) == 0.0


def test_a_renovated_house_near_augsburg_lands_where_the_bill_says() -> None:
    """A check against real numbers: an effective 245 W/K at G20/15 gives the
    ~22,000 kWh a year of space heat a 1970 house near Augsburg burns gas for,
    and the renovated house at 150 W/K, a 12.5 °C limit and 3,000 kWh of hot
    water needs a third of that in electricity, a fifth to a third of it from
    the roof (most of the heat is needed when the sun is low)."""
    hours = weather_year()
    today = compare(hours, BATTERY, Scenario(heat_pump=HeatPump(245.0)), PRICE, FEED_IN)
    renovated = compare(
        hours,
        BATTERY,
        Scenario(heat_pump=HeatPump(150.0, 12.5, hot_water_kwh_per_day=8.2)),
        PRICE,
        FEED_IN,
    )

    assert today.degree_hours_g20_15 is not None
    assert 3_300 < today.degree_hours_g20_15 / 24 < 4_300  # Kd
    assert 19_000 < today.changed.space_heat_kwh < 25_000
    o = renovated.changed
    assert 10_000 < o.space_heat_kwh < 14_000
    assert o.water_heat_kwh == pytest.approx(8.2 * 365, rel=0.01)
    assert o.seasonal_cop is not None
    assert 2.8 < o.seasonal_cop < 3.6
    share = renovated.heat_pump_solar_share
    assert share is not None
    assert 0.15 < share < 0.35


def test_hot_water_in_the_sun_takes_more_from_the_roof() -> None:
    hours = weather_year()
    spread = HeatPump(150.0, 12.5, hot_water_kwh_per_day=8.0)
    sunny = replace(spread, hot_water_at_noon=True)

    a = compare(hours, BATTERY, Scenario(heat_pump=spread), PRICE, FEED_IN)
    b = compare(hours, BATTERY, Scenario(heat_pump=sunny), PRICE, FEED_IN)

    assert b.changed.water_heat_kwh == pytest.approx(a.changed.water_heat_kwh)
    assert b.heat_pump_solar_share > a.heat_pump_solar_share
    assert b.heat_pump_cost_per_year < a.heat_pump_cost_per_year


def test_a_small_heat_pump_leaves_the_coldest_hours_to_the_heating_rod() -> None:
    hours = weather_year()
    big = HeatPump(250.0)
    small = replace(big, capacity_kw=4.0)

    a = replay(hours, BATTERY, big)
    b = replay(hours, BATTERY, small)

    assert a.backup_kwh == 0.0
    assert b.backup_kwh > 0
    assert b.heat_kwh == pytest.approx(a.heat_kwh)
    assert b.heat_pump_kwh > a.heat_pump_kwh
    assert b.seasonal_cop < a.seasonal_cop


def test_a_battery_and_a_heat_pump_are_priced_apart() -> None:
    hours = weather_year()
    pump = HeatPump(150.0, 12.5, hot_water_kwh_per_day=8.0)

    battery_only = compare(
        hours, BATTERY, Scenario(extra_battery_kwh=5.0), PRICE, FEED_IN
    )
    pump_only = compare(hours, BATTERY, Scenario(heat_pump=pump), PRICE, FEED_IN)
    both = compare(
        hours, BATTERY, Scenario(extra_battery_kwh=5.0, heat_pump=pump), PRICE, FEED_IN
    )

    # Together, the savings add up to the whole change.
    assert both.saving_per_year == pytest.approx(
        both.battery_saving_per_year - pump_only.heat_pump_cost_per_year
    )
    # With the heat pump's nights to cover, the battery is worth no less.
    assert both.battery_saving_per_year >= battery_only.battery_saving_per_year - 1e-6
    # The bigger battery lets more of the heat pump run on the roof's power.
    assert both.heat_pump_solar_share >= pump_only.heat_pump_solar_share
    assert both.heat_pump_cost_per_year <= pump_only.heat_pump_cost_per_year


def test_the_months_add_up_to_the_year() -> None:
    hours = weather_year()
    result = compare(
        hours,
        BATTERY,
        Scenario(heat_pump=HeatPump(150.0, 12.5, hot_water_kwh_per_day=8.0)),
        PRICE,
        FEED_IN,
    )

    months = result.months()
    assert [m.month for m in months] == list(range(1, 13))
    assert sum(m.heat_pump_kwh for m in months) == pytest.approx(
        result.changed.heat_pump_kwh
    )
    assert sum(m.import_kwh for m in months) == pytest.approx(result.changed.import_kwh)
    january, june = months[0], months[5]
    assert january.heat_pump_kwh > 3 * june.heat_pump_kwh
    assert (
        january.heat_pump_solar_kwh / january.heat_pump_kwh
        < june.heat_pump_solar_kwh / june.heat_pump_kwh
    )


def test_against_gas_the_heat_pump_saves_when_gas_is_dear_enough() -> None:
    hours = weather_year()
    result = compare(
        hours, BATTERY, Scenario(heat_pump=HeatPump(150.0, 12.5)), PRICE, FEED_IN
    )

    cheap = result.gas_cost_per_year(Gas(0.05))
    dear = result.gas_cost_per_year(Gas(0.15, standing_per_year=150))

    assert cheap is not None
    assert dear is not None
    assert cheap < result.heat_pump_cost_per_year < dear
    assert dear == pytest.approx(result.changed.heat_kwh / 0.9 * 0.15 + 150)


def test_a_bigger_battery_cycles_less_for_the_same_house() -> None:
    hours = year()
    result = compare(hours, BATTERY, Scenario(extra_battery_kwh=10.0), PRICE, FEED_IN)

    assert 50 < result.now.battery_cycles < 365
    assert result.changed.battery_cycles < result.now.battery_cycles


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
    hours = year()[200 * 24 : 260 * 24]  # summer, when there is plenty to sell

    free = replay(hours, BATTERY)
    capped = replay(hours, BATTERY, export_cap_kw=1.0)

    assert capped.export_kwh < free.export_kwh
    assert capped.import_kwh == pytest.approx(free.import_kwh)
