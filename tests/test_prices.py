"""Prices turn the plan's kWh into money, and never change the plan."""

from __future__ import annotations

from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from custom_components.solar_energy_insights import const
from tests.common import (
    DAY,
    local,
    recent,
    record_house,
    set_states,
    setup_entry,
    state,
)

PREFIX = "solar_energy_insights_"
GRID_COST = f"sensor.{PREFIX}grid_cost_until_sunrise"
SAVING = f"sensor.{PREFIX}saving_from_using_the_surplus"
PLAN_ENTITIES = (
    f"sensor.{PREFIX}nights_covered",
    f"sensor.{PREFIX}spare_at_sunrise",
    f"sensor.{PREFIX}charge_needed_for_the_night",
    f"sensor.{PREFIX}battery_full_at",
    f"sensor.{PREFIX}best_window_for_heavy_loads",
)


def heavy_evening(hour: int) -> float:
    return 1.2 if hour >= 18 or hour < 6 else 0.4


async def evening_home(
    home: HomeAssistant, freezer: FrozenDateTimeFactory, options: dict[str, Any]
) -> None:
    """A low battery at 21:00 before a heavy night: the grid will be needed."""
    await record_house(home, recent(14), heavy_evening)
    freezer.move_to(local(DAY, 21))
    set_states(home, soc=25)
    await setup_entry(home, options)


def grid_kwh(home: HomeAssistant) -> float:
    spare = home.states.get(f"sensor.{PREFIX}spare_at_sunrise")
    assert spare is not None
    return float(spare.attributes["grid_kwh"])


async def test_the_night_costs_its_grid_energy_at_the_fixed_rate(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await evening_home(home, freezer, {const.CONF_PRICE: 40.0})

    assert grid_kwh(home) > 0
    assert float(state(home, GRID_COST)) == pytest.approx(
        grid_kwh(home) * 0.40, abs=0.01
    )


@pytest.mark.parametrize(
    ("value", "unit", "per_kwh"),
    (("55", "ct/kWh", 0.55), ("0.55", "EUR/kWh", 0.55)),
)
async def test_a_price_entity_overrides_the_fixed_rate(
    home: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    value: str,
    unit: str,
    per_kwh: float,
) -> None:
    home.states.async_set("sensor.spot_price", value, {"unit_of_measurement": unit})

    await evening_home(
        home,
        freezer,
        {const.CONF_PRICE: 30.0, const.CONF_PRICE_ENTITY: "sensor.spot_price"},
    )

    cost = home.states.get(GRID_COST)
    assert cost is not None
    assert cost.attributes["price_source"] == "entity"
    assert float(cost.state) == pytest.approx(grid_kwh(home) * per_kwh, abs=0.01)


async def test_an_unavailable_price_entity_falls_back_to_the_fixed_rate(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    home.states.async_set("sensor.spot_price", "unavailable")

    await evening_home(
        home,
        freezer,
        {const.CONF_PRICE: 30.0, const.CONF_PRICE_ENTITY: "sensor.spot_price"},
    )

    cost = home.states.get(GRID_COST)
    assert cost is not None
    assert cost.attributes["price_source"] == "fixed"
    assert float(cost.state) == pytest.approx(grid_kwh(home) * 0.30, abs=0.01)


async def test_using_the_surplus_saves_the_gap_between_price_and_feed_in(
    home: HomeAssistant,
) -> None:
    await record_house(home, recent(14), heavy_evening)
    set_states(home, soc=90, peak_kw=9)

    await setup_entry(home, {const.CONF_PRICE: 30.0, const.CONF_FEED_IN: 8.0})

    saving = home.states.get(SAVING)
    assert saving is not None
    surplus = float(state(home, f"sensor.{PREFIX}solar_surplus_until_sunset"))
    assert surplus > 0
    assert float(saving.state) == pytest.approx(surplus * 0.22, abs=0.01)
    assert saving.attributes["if_exported"] == pytest.approx(surplus * 0.08, abs=0.01)


async def test_prices_never_change_the_plan(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await evening_home(home, freezer, {const.CONF_PRICE: 10.0, const.CONF_FEED_IN: 2.0})
    cheap = {entity_id: state(home, entity_id) for entity_id in PLAN_ENTITIES}
    entry = home.config_entries.async_entries(const.DOMAIN)[0]

    home.config_entries.async_update_entry(
        entry, options={const.CONF_PRICE: 90.0, const.CONF_FEED_IN: 50.0}
    )
    await home.async_block_till_done(wait_background_tasks=True)

    assert {entity_id: state(home, entity_id) for entity_id in PLAN_ENTITIES} == cheap


async def test_simulate_reports_what_the_extra_load_costs(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await evening_home(home, freezer, {const.CONF_PRICE: 40.0})

    alone: Any = await home.services.async_call(
        const.DOMAIN, "simulate", {"extra_kwh": 0}, blocking=True, return_response=True
    )
    dryer: Any = await home.services.async_call(
        const.DOMAIN,
        "simulate",
        {"extra_kwh": 3, "extra_hours": 2},
        blocking=True,
        return_response=True,
    )

    assert dryer["costs"]["night_grid"] > alone["costs"]["night_grid"]
    assert dryer["costs"]["price"] == pytest.approx(0.40)
