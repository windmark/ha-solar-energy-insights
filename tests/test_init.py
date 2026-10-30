"""The integration answers from live states and the recorder's recent nights."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

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

LASTS = "binary_sensor.solar_energy_insights_battery_lasts_the_night"
COVERED = "sensor.solar_energy_insights_nights_covered"
FULL_AT = "sensor.solar_energy_insights_battery_full_at"


def quiet(hour: int) -> float:
    return 0.3 if hour < 17 else 0.6


def heavy(hour: int) -> float:
    return 2.5 if hour >= 18 or hour < 6 else 0.5


def moderate(hour: int) -> float:
    """About 8 kWh from 20:00 to sunrise: more than a low battery holds, less than a
    full one."""
    return 0.9 if hour >= 18 or hour < 6 else 0.5


async def test_sets_up_and_unloads(home: HomeAssistant) -> None:
    set_states(home)

    entry = await setup_entry(home)

    assert entry.state is ConfigEntryState.LOADED
    assert state(home, FULL_AT) not in ("unknown", "unavailable")
    assert await home.config_entries.async_unload(entry.entry_id)


async def test_waits_for_the_battery(home: HomeAssistant) -> None:
    set_states(home)
    home.states.async_set("sensor.battery_soc", "unavailable")

    entry = await setup_entry(home)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_without_history_the_night_is_unknown(home: HomeAssistant) -> None:
    set_states(home)

    await setup_entry(home)

    assert state(home, LASTS) == "unknown"
    assert state(home, COVERED) == "unknown"


async def test_a_quiet_house_with_a_full_battery_lasts(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), quiet)
    freezer.move_to(local(DAY, 20))
    set_states(home, soc=90)

    await setup_entry(home)

    assert state(home, LASTS) == "on"
    assert state(home, COVERED) == "100"


async def test_a_heavy_house_on_a_low_battery_does_not(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), heavy)
    freezer.move_to(local(DAY, 20))
    set_states(home, soc=30)

    await setup_entry(home)

    assert state(home, LASTS) == "off"
    assert state(home, "sensor.solar_energy_insights_reserve_reached_at") not in (
        "unknown",
        "unavailable",
    )


async def test_follows_the_battery_live(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), moderate)
    freezer.move_to(local(DAY, 20))
    set_states(home, soc=30)
    await setup_entry(home)
    assert state(home, COVERED) == "0"

    set_states(home, soc=100)
    freezer.tick(60)
    async_fire_time_changed(home)
    await home.async_block_till_done()

    assert state(home, COVERED) == "100"


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
