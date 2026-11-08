"""A feed-in cap: what would be clipped until sunset, and what was curtailed today."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from custom_components.solar_energy_insights import const
from tests.common import (
    DAY,
    ENTITIES,
    local,
    recent,
    record_house,
    set_states,
    setup_entry,
    state,
)
from tests.test_answers import evening_load
from tests.test_config_flow import suggested

PREFIX = "solar_energy_insights_"
CAP = "sensor.powerocean_maximum_feed_in_power"
SOLAR = "sensor.powerocean_solar_power"
CLIPPING = f"sensor.{PREFIX}clipping_until_sunset"
CURTAILED = f"sensor.{PREFIX}curtailed_today"
OPTIONS = {const.CONF_FEED_IN_CAP: CAP, const.CONF_SOLAR_POWER: SOLAR}


def set_power(home: HomeAssistant, entity_id: str, watts: float) -> None:
    home.states.async_set(
        entity_id,
        str(watts),
        {
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )


async def test_a_full_battery_on_a_clear_day_would_be_clipped(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 9))
    set_states(home, soc=95, peak_kw=12)
    set_power(home, CAP, 6000)

    await setup_entry(home, OPTIONS)

    clipping = home.states.get(CLIPPING)
    assert clipping is not None
    assert float(clipping.state) > 1.0
    assert clipping.attributes["cap_kw"] == 6.0
    assert clipping.attributes["from"] is not None
    assert clipping.attributes["until"] is not None


async def test_a_cap_above_the_peak_clips_nothing(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home, soc=95, peak_kw=8)
    set_power(home, CAP, 12000)

    await setup_entry(home, OPTIONS)

    assert float(state(home, CLIPPING)) == 0.0


async def test_without_a_cap_there_are_no_clipping_sensors(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)

    await setup_entry(home)

    assert home.states.get(CLIPPING) is None
    assert home.states.get(CURTAILED) is None


async def test_a_new_cap_replans_at_once(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home, soc=95, peak_kw=12)
    set_power(home, CAP, 6000)
    await setup_entry(home, OPTIONS)
    before = float(state(home, CLIPPING))

    set_power(home, CAP, 9000)
    await home.async_block_till_done(wait_background_tasks=True)

    assert float(state(home, CLIPPING)) < before


async def test_solar_held_at_the_cap_is_counted_as_curtailed(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    start = local(DAY, 12)
    freezer.move_to(start)
    set_states(home, soc=100, peak_kw=12, grid_w=-6000)
    set_power(home, CAP, 6000)
    # Exporting at the cap, the house using 0.5 kW: the roof is held at 6.5 kW
    # while the forecast says more than 11.
    set_power(home, ENTITIES[const.CONF_GRID_POWER], -6000)
    set_power(home, SOLAR, 6500)
    await async_wait_recording_done(home)
    for k in range(6):
        freezer.move_to(start + timedelta(minutes=5 * (k + 1)))
        do_adhoc_statistics(
            home, start=dt_util.as_utc(start + timedelta(minutes=5 * k))
        )
        await async_wait_recording_done(home)

    await setup_entry(home, OPTIONS)

    curtailed = home.states.get(CURTAILED)
    assert curtailed is not None
    assert curtailed.attributes["minutes_at_cap"] == 30
    # About 5 kW held back for half an hour.
    assert 2.0 < float(curtailed.state) < 3.0


async def test_below_the_cap_nothing_is_curtailed(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    start = local(DAY, 12)
    freezer.move_to(start)
    set_states(home, soc=100, peak_kw=12)
    set_power(home, CAP, 9000)
    set_power(home, ENTITIES[const.CONF_GRID_POWER], -4000)
    set_power(home, SOLAR, 4500)
    await async_wait_recording_done(home)
    for k in range(3):
        freezer.move_to(start + timedelta(minutes=5 * (k + 1)))
        do_adhoc_statistics(
            home, start=dt_util.as_utc(start + timedelta(minutes=5 * k))
        )
        await async_wait_recording_done(home)

    await setup_entry(home, OPTIONS)

    assert float(state(home, CURTAILED)) == 0.0


async def test_options_suggest_the_powerocean_feed_in_cap(home: HomeAssistant) -> None:
    set_states(home)
    cap = er.async_get(home).async_get_or_create(
        "sensor", "ef_powerocean_tcpmodbus", "abc_feed_in_power_max"
    )
    er.async_get(home).async_get_or_create(
        "sensor", "ef_powerocean_tcpmodbus", "abc_feed_in_power_max_setting"
    )
    entry = await setup_entry(home)

    result = await home.config_entries.options.async_init(entry.entry_id)

    assert suggested(result, const.CONF_FEED_IN_CAP) == cap.entity_id
