"""The integration answers from live states and the recorder's recent nights."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

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


async def test_waits_for_a_battery_it_has_never_seen(home: HomeAssistant) -> None:
    set_states(home, soc="unavailable", capacity_wh="unavailable")

    entry = await setup_entry(home)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_battery_switched_off_below_its_reserve_is_remembered_from_the_recorder(
    home: HomeAssistant,
) -> None:
    """Asleep below its reserve, the battery's entities go unavailable, and Home
    Assistant may start while it sleeps: its last readings stand."""
    set_states(home, soc=9.0, capacity_wh=10000, reserve=10.0)
    await async_wait_recording_done(home)
    set_states(
        home, soc="unavailable", capacity_wh="unavailable", reserve="unavailable"
    )

    entry = await setup_entry(home)

    assert entry.state is ConfigEntryState.LOADED
    battery = entry.runtime_data.battery()
    assert battery.capacity_kwh == 10.0
    assert battery.floor_pct == 10.0
    # It switched off just below its reserve; that is where it is.
    assert battery.soc_pct == 9.0
    assert entry.runtime_data.remembered == {"capacity_kwh", "soc_pct", "reserve_pct"}
    needed = home.states.get("sensor.solar_energy_insights_charge_needed_for_the_night")
    assert needed is not None
    assert needed.attributes["battery_now"] == 9.0
    assert needed.attributes["battery_remembered"] is True


async def test_readings_are_kept_while_the_battery_sleeps(home: HomeAssistant) -> None:
    set_states(home, soc=42.0, capacity_wh=10000, reserve=10.0)
    entry = await setup_entry(home)
    coordinator = entry.runtime_data

    set_states(
        home, soc="unavailable", capacity_wh="unavailable", reserve="unavailable"
    )
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    battery = coordinator.battery()
    assert battery.capacity_kwh == 10.0
    assert battery.soc_pct == 42.0
    assert coordinator.memory.memory.battery == {
        "capacity_kwh": 10.0,
        "soc_pct": 42.0,
        "reserve_pct": 10.0,
    }


async def test_a_live_reading_replaces_the_remembered_one(home: HomeAssistant) -> None:
    set_states(home, soc=42.0, capacity_wh=10000)
    entry = await setup_entry(home)
    coordinator = entry.runtime_data
    set_states(home, soc="unavailable", capacity_wh="unavailable")
    await coordinator.async_refresh()

    set_states(home, soc=55.0, capacity_wh=10000)
    await coordinator.async_refresh()

    assert coordinator.battery().soc_pct == 55.0
    assert coordinator.remembered == set()


NO_RESERVE = {const.CONF_MIN_RESERVE: 0.0}
NEEDED = "sensor.solar_energy_insights_charge_needed_for_the_night"


async def test_a_battery_held_above_its_reserve_is_empty(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Some inverters stop discharging a few percent above the reserve they
    report and leave the house on the grid: the battery is empty there."""
    set_states(home, soc=1.0, reserve=0.0, grid_w=400)
    entry = await setup_entry(home, NO_RESERVE)
    coordinator = entry.runtime_data
    assert coordinator.battery().floor_pct == 0.0

    freezer.tick(timedelta(minutes=20))
    await coordinator.async_refresh()
    await home.async_block_till_done()

    assert coordinator.held_pct == 1.0
    battery = coordinator.battery()
    assert battery.floor_pct == 1.0
    assert battery.usable_kwh == 0
    # Past days are judged against the reserve the battery reports.
    assert coordinator.battery(as_reported=True).floor_pct == 0.0
    needed = home.states.get(NEEDED)
    assert needed is not None
    assert needed.attributes["battery_held"] is True


async def test_a_restart_does_not_restart_the_wait_for_a_held_battery(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Updating the integration restarts Home Assistant, which gives the level a
    new last change though it has sat at 1 % all evening."""
    set_states(home, soc=1.0, reserve=0.0, grid_w=400)
    await async_wait_recording_done(home)
    freezer.tick(timedelta(minutes=30))
    set_states(home, soc="unavailable", reserve=0.0, grid_w=400)
    set_states(home, soc=1.0, reserve=0.0, grid_w=400)
    await async_wait_recording_done(home)

    entry = await setup_entry(home, NO_RESERVE)

    assert entry.runtime_data.held_pct == 1.0
    assert entry.runtime_data.battery().floor_pct == 1.0


async def test_a_dropout_does_not_restart_the_wait_for_a_held_battery(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    set_states(home, soc=1.0, reserve=0.0, grid_w=400)
    entry = await setup_entry(home, NO_RESERVE)
    coordinator = entry.runtime_data
    freezer.tick(timedelta(minutes=10))
    set_states(home, soc="unavailable", reserve=0.0, grid_w=400)
    await coordinator.async_refresh()

    set_states(home, soc=1.0, reserve=0.0, grid_w=400)
    freezer.tick(timedelta(minutes=10))
    await coordinator.async_refresh()

    assert coordinator.held_pct == 1.0


async def test_a_held_battery_is_released_once_its_level_moves(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    set_states(home, soc=1.0, reserve=0.0, grid_w=400)
    entry = await setup_entry(home, NO_RESERVE)
    coordinator = entry.runtime_data
    freezer.tick(timedelta(minutes=20))
    await coordinator.async_refresh()
    assert coordinator.held_pct == 1.0

    set_states(home, soc=2.0, reserve=0.0, grid_w=-1500)
    await coordinator.async_refresh()

    assert coordinator.held_pct is None
    assert coordinator.battery().floor_pct == 0.0


async def test_a_low_battery_still_covering_the_house_is_not_held(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    set_states(home, soc=2.0, reserve=0.0, grid_w=10)
    entry = await setup_entry(home, NO_RESERVE)
    coordinator = entry.runtime_data

    freezer.tick(timedelta(minutes=30))
    await coordinator.async_refresh()

    assert coordinator.held_pct is None
    assert coordinator.battery().floor_pct == 0.0


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
