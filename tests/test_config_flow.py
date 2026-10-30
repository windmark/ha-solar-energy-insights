"""Setting up: the preset, the forecast check, options and appliances."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.solar_energy_insights import const
from tests.common import ENTITIES, set_states, setup_entry

BATTERY_STEP = {
    key: ENTITIES[key]
    for key in (
        const.CONF_BATTERY_SOC,
        const.CONF_BATTERY_POWER,
        const.CONF_BATTERY_CAPACITY,
        const.CONF_SOLAR_POWER,
        const.CONF_HOUSE_POWER,
        const.CONF_GRID_POWER,
    )
}
ENERGY_STEP = {key: ENTITIES[key] for key in const.ENERGY_KEYS}
FORECAST_STEP = {
    const.CONF_SOLCAST_TODAY: ENTITIES[const.CONF_SOLCAST_TODAY],
    const.CONF_SOLCAST_TOMORROW: ENTITIES[const.CONF_SOLCAST_TOMORROW],
}


def schema_defaults(result: Any) -> dict[str, Any]:
    schema = result["data_schema"]
    assert schema is not None
    return dict(schema({}))


def suggested(result: Any, field: str) -> str | None:
    for key in result["data_schema"].schema:
        if key == field:
            return (key.description or {}).get("suggested_value")
    return None


async def test_creates_an_entry_in_three_steps(home: HomeAssistant) -> None:
    set_states(home)
    result = await home.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await home.config_entries.flow.async_configure(
        result["flow_id"], BATTERY_STEP
    )
    result = await home.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_STEP
    )
    result = await home.config_entries.flow.async_configure(
        result["flow_id"], FORECAST_STEP
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {**BATTERY_STEP, **ENERGY_STEP, **FORECAST_STEP}


async def test_prefills_from_powerocean_and_solcast(home: HomeAssistant) -> None:
    set_states(home)
    registry = er.async_get(home)
    soc = registry.async_get_or_create(
        "sensor", "ef_powerocean_tcpmodbus", "abc_battery_soc"
    )
    reserve = registry.async_get_or_create(
        "sensor", "ef_powerocean_tcpmodbus", "abc_battery_reserve_effective"
    )
    registry.async_get_or_create(
        "sensor", "ef_powerocean_tcpmodbus", "abc_min_soc_limit"
    )

    result = await home.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert suggested(result, const.CONF_BATTERY_SOC) == soc.entity_id
    # The reserve in force wins over the app's own setting.
    assert suggested(result, const.CONF_BATTERY_RESERVE) == reserve.entity_id
    result = await home.config_entries.flow.async_configure(
        result["flow_id"], BATTERY_STEP
    )
    result = await home.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_STEP
    )
    assert (
        suggested(result, const.CONF_SOLCAST_TODAY)
        == ENTITIES[const.CONF_SOLCAST_TODAY]
    )


async def test_refuses_a_forecast_without_solcast_detail(home: HomeAssistant) -> None:
    set_states(home)
    home.states.async_set("sensor.some_forecast", "20", {"unit_of_measurement": "kWh"})
    result = await home.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await home.config_entries.flow.async_configure(
        result["flow_id"], BATTERY_STEP
    )
    result = await home.config_entries.flow.async_configure(
        result["flow_id"], ENERGY_STEP
    )

    result = await home.config_entries.flow.async_configure(
        result["flow_id"],
        {**FORECAST_STEP, const.CONF_SOLCAST_TODAY: "sensor.some_forecast"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {const.CONF_SOLCAST_TODAY: "not_solcast"}


async def test_only_one_instance(home: HomeAssistant) -> None:
    set_states(home)
    await setup_entry(home)

    result = await home.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_change_the_plan_inputs(home: HomeAssistant) -> None:
    set_states(home)
    entry = await setup_entry(home)
    before = entry.runtime_data.price_eur_kwh

    result = await home.config_entries.options.async_init(entry.entry_id)
    result = await home.config_entries.options.async_configure(
        result["flow_id"], {**schema_defaults(result), const.CONF_PRICE: 45.0}
    )
    await home.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.price_eur_kwh > before


@pytest.mark.parametrize(
    ("appliance", "created"),
    (
        (
            {
                const.CONF_NAME: "Dishwasher",
                const.CONF_POWER_ENTITY: "sensor.house_power",
            },
            True,
        ),
        (
            {const.CONF_NAME: "Washer", const.CONF_STATE_ENTITY: "sensor.battery_soc"},
            True,
        ),
        ({const.CONF_NAME: "Nothing to watch"}, False),
    ),
)
async def test_an_appliance_needs_something_to_watch(
    home: HomeAssistant, appliance: dict, created: bool
) -> None:
    set_states(home)
    entry = await setup_entry(home)

    result = await home.config_entries.subentries.async_init(
        (entry.entry_id, const.SUBENTRY_APPLIANCE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await home.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            const.CONF_DEFAULT_KWH: 1.0,
            const.CONF_DEFAULT_HOURS: 2.0,
            const.CONF_MAX_KWH: 4.0,
            **appliance,
        },
    )
    await home.async_block_till_done(wait_background_tasks=True)

    assert (result["type"] is FlowResultType.CREATE_ENTRY) is created
    assert len(entry.subentries) == int(created)
