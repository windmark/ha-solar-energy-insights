"""Appliances added under the integration: when to start each, and whether now
is a good time."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.solar_energy_insights import const
from tests.common import DAY, local, recent, record_house, set_states, setup_entry
from tests.test_answers import evening_load

BEST = "sensor.dishwasher_best_start"
GOOD = "binary_sensor.dishwasher_good_time_to_start"
DISHWASHER = {"name": "Dishwasher", "icon": "mdi:dishwasher", "kwh": 1.2, "hours": 2}


async def add_appliance(
    home: HomeAssistant, entry_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    result = await home.config_entries.subentries.async_init(
        (entry_id, const.SUBENTRY_APPLIANCE), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await home.config_entries.subentries.async_configure(
        result["flow_id"], data
    )
    await home.async_block_till_done(wait_background_tasks=True)
    return dict(result)


async def test_an_added_appliance_gets_its_own_device_and_sensors(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 12))
    set_states(home, soc=100, peak_kw=9)
    entry = await setup_entry(home)

    result = await add_appliance(home, entry.entry_id, DISHWASHER)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dishwasher"
    best, good = home.states.get(BEST), home.states.get(GOOD)
    assert best is not None
    assert good is not None
    assert best.attributes["kwh"] == 1.2
    assert best.attributes["free_share"] == 100
    # Full at noon with the sun to spare: now is as good as it gets.
    assert good.state == "on"
    registry = er.async_get(home).async_get(BEST)
    assert registry is not None
    device = dr.async_get(home).async_get(str(registry.device_id))
    assert device is not None
    assert device.name == "Dishwasher"


async def test_while_the_battery_charges_it_is_not_yet_a_good_time(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 7))
    set_states(home, soc=15, peak_kw=6)
    entry = await setup_entry(home)

    await add_appliance(home, entry.entry_id, DISHWASHER)

    good = home.states.get(GOOD)
    assert good is not None
    assert good.state == "off"
    assert good.attributes["best_start"] is not None
    assert good.attributes["now_share"] < good.attributes["free_share"]


async def test_a_sensor_that_learns_the_run_beats_the_typical_figures(
    home: HomeAssistant,
) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home, soc=100, peak_kw=9)
    home.states.async_set(
        "sensor.dishwasher_energy_per_run",
        "0.8",
        {"unit_of_measurement": "kWh", "hours": [1.5, 1.5]},
    )
    entry = await setup_entry(home)

    await add_appliance(
        home,
        entry.entry_id,
        {**DISHWASHER, "energy_per_run": "sensor.dishwasher_energy_per_run"},
    )

    best = home.states.get(BEST)
    assert best is not None
    assert best.attributes["kwh"] == 0.8
    assert best.attributes["hours"] == 1.5
    assert best.attributes["estimated"] is False


async def test_an_appliance_can_be_changed(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home, soc=100, peak_kw=9)
    entry = await setup_entry(home)
    await add_appliance(home, entry.entry_id, DISHWASHER)
    subentry_id = next(iter(entry.subentries))

    result = await home.config_entries.subentries.async_init(
        (entry.entry_id, const.SUBENTRY_APPLIANCE),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    result = await home.config_entries.subentries.async_configure(
        result["flow_id"], {**DISHWASHER, "kwh": 2.0}
    )
    await home.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    best = home.states.get(BEST)
    assert best is not None
    assert best.attributes["kwh"] == 2.0


async def test_the_cards_get_the_appliances(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)
    entry = await setup_entry(home)
    await add_appliance(home, entry.entry_id, DISHWASHER)
    client = await hass_ws_client(home)

    await client.send_json_auto_id({"type": f"{const.DOMAIN}/config"})
    response = await client.receive_json()

    assert response["success"]
    assert response["result"]["appliances"] == [
        {"name": "Dishwasher", "icon": "mdi:dishwasher", "kwh": 1.2, "hours": 2}
    ]


async def test_without_appliances_there_are_none(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)

    await setup_entry(home)

    assert home.states.get(BEST) is None
    assert home.states.get(GOOD) is None
