"""The charge limit, read from an entity or the options."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.solar_energy_insights import const
from tests.common import (
    DAY,
    ENTITIES,
    local,
    recent,
    record_battery,
    record_house,
    set_states,
    setup_entry,
)
from tests.test_answers import evening_load
from tests.test_config_flow import suggested

PREFIX = "solar_energy_insights_"
LIMIT = "number.powerocean_charge_limit_soc"


async def setup_with_limit(home: HomeAssistant, options: dict[str, Any] | None = None):
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        title="Solar Energy Insights",
        data={**ENTITIES, const.CONF_CHARGE_LIMIT: LIMIT},
        options=options or {},
    )
    entry.add_to_hass(home)
    await home.config_entries.async_setup(entry.entry_id)
    await home.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_the_battery_is_planned_to_its_limit(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 8))
    set_states(home, soc=30, peak_kw=9)
    home.states.async_set(LIMIT, "80", {"unit_of_measurement": "%"})

    entry = await setup_with_limit(home)

    plan = entry.runtime_data.plan
    assert plan is not None
    assert plan.charge_limit_pct == 80
    assert plan.charge.peak_pct <= 80.01


async def test_without_a_value_the_option_is_used(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)
    home.states.async_set(LIMIT, "unavailable")

    entry = await setup_with_limit(home, {const.CONF_MAX_CHARGE: 90})

    assert entry.runtime_data.plan is not None
    assert entry.runtime_data.plan.charge_limit_pct == 90


async def test_a_new_limit_replans_at_once(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)
    home.states.async_set(LIMIT, "100", {"unit_of_measurement": "%"})
    entry = await setup_with_limit(home)

    home.states.async_set(LIMIT, "85", {"unit_of_measurement": "%"})
    await home.async_block_till_done(wait_background_tasks=True)

    assert entry.runtime_data.plan is not None
    assert entry.runtime_data.plan.charge_limit_pct == 85


async def test_setup_finds_powerocean_charge_limit(home: HomeAssistant) -> None:
    set_states(home)
    limit = er.async_get(home).async_get_or_create(
        "number", "ef_powerocean_tcpmodbus", "abc_charge_limit_soc"
    )

    result = await home.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert suggested(result, const.CONF_CHARGE_LIMIT) == limit.entity_id


def hungry(hour: int) -> float:
    return 2.5 if hour >= 18 or hour < 6 else 0.5


async def test_charge_needed_reads_the_limit_when_a_heavy_night_is_short(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), hungry)
    freezer.move_to(local(DAY, 21))
    set_states(home, soc=60)

    await setup_entry(home, {const.CONF_MAX_CHARGE: 80})

    needed = home.states.get(f"sensor.{PREFIX}charge_needed_for_the_night")
    assert needed is not None
    assert float(needed.state) == 80
    assert needed.attributes["charge_limit"] == 80
    assert needed.attributes["full_battery_lasts"] is False


def to_eighty(hour: int) -> float:
    """Charges to an 80 % limit by 13:00, then drains."""
    if hour < 9:
        return 25.0
    if hour <= 13:
        return min(25.0 + (hour - 9) * 14, 80.0)
    return max(80.0 - (hour - 13) * 6, 10.0)


async def test_full_at_is_scored_against_the_limit(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 8))
    set_states(home, soc=25, peak_kw=9)
    await setup_entry(home, {const.CONF_MAX_CHARGE: 80})

    await record_battery(home, [DAY], to_eighty)
    freezer.move_to(local(DAY + timedelta(days=1), 10, 6))
    async_fire_time_changed(home)
    await home.async_block_till_done(wait_background_tasks=True)

    error = home.states.get(f"sensor.{PREFIX}full_at_error")
    assert error is not None
    assert error.attributes["days"] >= 1
    # Reaching 80 % was full: not a day where "full" was wrong.
    assert error.attributes["missed"] == 0
