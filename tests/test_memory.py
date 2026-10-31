"""What is remembered between restarts, and the plan's own answers scored."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.solar_energy_insights import const
from tests.common import (
    DAY,
    local,
    recent,
    record_battery,
    record_house,
    set_states,
    setup_entry,
    state,
)
from tests.test_answers import draining, evening_load

PREFIX = "solar_energy_insights_"


async def test_the_morning_forecast_and_full_at_are_kept(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 8))
    set_states(home, soc=30, peak_kw=9)

    entry = await setup_entry(home)

    memory = entry.runtime_data.memory.memory
    assert DAY.isoformat() in memory.days
    assert memory.days[DAY.isoformat()]["full_at"] is not None
    assert len(memory.forecasts[DAY.isoformat()]) > 20


async def test_an_afternoon_start_keeps_no_morning(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 15))
    set_states(home)

    entry = await setup_entry(home)

    assert DAY.isoformat() not in entry.runtime_data.memory.memory.days


async def test_the_night_answer_is_kept_after_21(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 21, 10))
    set_states(home, soc=70)

    entry = await setup_entry(home)

    night = entry.runtime_data.memory.memory.nights[DAY.isoformat()]
    assert night["lasts"] in (True, False)
    assert night["spare_kwh"] >= 0


async def test_the_mornings_full_at_is_scored_the_next_day(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 8))
    set_states(home, soc=30, peak_kw=9)
    await setup_entry(home)

    # The day happens: the battery's record arrives, and the clock moves on.
    await record_battery(home, [DAY], draining)
    freezer.move_to(local(DAY + timedelta(days=1), 10, 6))
    async_fire_time_changed(home)
    await home.async_block_till_done(wait_background_tasks=True)

    error = home.states.get(f"sensor.{PREFIX}full_at_error")
    assert error is not None
    assert error.attributes["days"] >= 1
    assert float(error.state) >= 0


async def test_the_solcast_correction_says_how_far_it_has_got(
    home: HomeAssistant,
) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)

    await setup_entry(home, {const.CONF_SOLAR_ENERGY: "sensor.house_energy"})

    correction = home.states.get(f"sensor.{PREFIX}solcast_correction_gain")
    assert correction is not None
    assert correction.attributes["active"] is False
    assert correction.attributes["days"] == 0
    assert state(home, f"sensor.{PREFIX}battery_full_at") not in ("unavailable",)
