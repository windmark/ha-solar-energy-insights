"""The integration sets up on a real Home Assistant and answers from live states."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.solar_energy_insights import const
from tests.common import set_states, setup_entry, state


async def test_sets_up_and_unloads(home: HomeAssistant) -> None:
    set_states(home)

    entry = await setup_entry(home)

    assert entry.state is ConfigEntryState.LOADED
    states = [
        home.states.get(e.entity_id)
        for e in er.async_entries_for_config_entry(er.async_get(home), entry.entry_id)
    ]
    assert states
    assert all(state is not None for state in states)
    assert state(home, "sensor.solar_energy_insights_plan") not in (
        "unknown",
        "unavailable",
    )

    assert await home.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_waits_for_the_battery(home: HomeAssistant) -> None:
    set_states(home)
    home.states.async_set("sensor.battery_soc", "unavailable")

    entry = await setup_entry(home)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_sunny_morning_plans_a_full_battery(home: HomeAssistant) -> None:
    set_states(home, soc=40, peak_kw=9)

    await setup_entry(home)

    assert state(home, "sensor.solar_energy_insights_battery_full_at") not in (
        "unknown",
        "unavailable",
    )
    assert (
        state(home, "binary_sensor.solar_energy_insights_battery_lasts_the_night")
        == "on"
    )


async def test_follows_the_battery_live(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    set_states(home, soc=40)
    await setup_entry(home)
    before = state(home, "sensor.solar_energy_insights_battery_full_at")

    set_states(home, soc=95)
    freezer.tick(60)
    async_fire_time_changed(home)
    await home.async_block_till_done()

    assert state(home, "sensor.solar_energy_insights_battery_full_at") != before


async def test_free_power_is_announced(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events: list[Event] = []
    home.bus.async_listen(const.EVENT_FREE_POWER, events.append)
    set_states(home, soc=60)
    await setup_entry(home)

    set_states(home, soc=100, grid_w=-3000)
    freezer.tick(60)
    async_fire_time_changed(home)
    await home.async_block_till_done()

    assert state(home, "binary_sensor.solar_energy_insights_free_power") == "on"
    assert len(events) == 1
