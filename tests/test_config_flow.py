"""Setting up, prefilled from PowerOcean and Solcast."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.solar_energy_insights import const
from tests.common import ENTITIES, set_states, setup_entry


def suggested(result: Any, field: str) -> str | None:
    for key in result["data_schema"].schema:
        if key == field:
            return (key.description or {}).get("suggested_value")
    return None


async def start(home: HomeAssistant) -> Any:
    return await home.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_creates_an_entry(home: HomeAssistant) -> None:
    set_states(home)
    result = await start(home)

    result = await home.config_entries.flow.async_configure(result["flow_id"], ENTITIES)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTITIES


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

    result = await start(home)

    assert suggested(result, const.CONF_BATTERY_SOC) == soc.entity_id
    # The reserve in force wins over the app's own setting.
    assert suggested(result, const.CONF_BATTERY_RESERVE) == reserve.entity_id
    assert (
        suggested(result, const.CONF_SOLCAST_TODAY)
        == ENTITIES[const.CONF_SOLCAST_TODAY]
    )


async def test_refuses_a_forecast_without_solcast_detail(home: HomeAssistant) -> None:
    set_states(home)
    home.states.async_set("sensor.some_forecast", "20", {"unit_of_measurement": "kWh"})
    result = await start(home)

    result = await home.config_entries.flow.async_configure(
        result["flow_id"],
        {**ENTITIES, const.CONF_SOLCAST_TODAY: "sensor.some_forecast"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {const.CONF_SOLCAST_TODAY: "not_solcast"}


async def test_only_one_instance(home: HomeAssistant) -> None:
    set_states(home)
    await setup_entry(home)

    result = await start(home)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_set_how_many_nights_are_replayed(home: HomeAssistant) -> None:
    set_states(home)
    entry = await setup_entry(home)

    result = await home.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"]
    assert schema is not None
    result = await home.config_entries.options.async_configure(
        result["flow_id"], {**schema({}), const.CONF_NIGHTS: 21}
    )
    await home.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.nights == 21


async def test_prices_are_prefilled_from_existing_helpers(home: HomeAssistant) -> None:
    set_states(home)
    home.states.async_set("input_number.electricity_price", "32.4")
    home.states.async_set("input_number.feed_in_tariff", "7.9")
    entry = await setup_entry(home)

    result = await home.config_entries.options.async_init(entry.entry_id)

    assert suggested(result, const.CONF_PRICE) == 32.4
    assert suggested(result, const.CONF_FEED_IN) == 7.9


async def test_options_suggest_powerocean_counters_for_achievements(
    home: HomeAssistant,
) -> None:
    set_states(home)
    registry = er.async_get(home)
    imported = registry.async_get_or_create(
        "sensor", "ef_powerocean_tcpmodbus", "abc_grid_import_total"
    )
    solar = registry.async_get_or_create(
        "sensor", "ef_powerocean_tcpmodbus", "abc_solar_total"
    )
    entry = await setup_entry(home)

    result = await home.config_entries.options.async_init(entry.entry_id)

    assert suggested(result, const.CONF_GRID_IMPORT_ENERGY) == imported.entity_id
    assert suggested(result, const.CONF_SOLAR_ENERGY) == solar.entity_id
