"""What is read from the EcoFlow integrations and Solcast, and prefilled."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.solar_energy_insights import const
from custom_components.solar_energy_insights.core.array import plane_yield
from tests.common import recent, set_states, setup_entry
from tests.test_achievements import ON, history
from tests.test_config_flow import suggested

LATITUDE = 52.5


def solcast_site(
    hass: HomeAssistant, name: str, kwp: float, compass: float, tilt: float
) -> None:
    entry = er.async_get(hass).async_get_or_create(
        "sensor",
        "solcast_solar",
        f"solcast_solcast_api_{name}",
        suggested_object_id=name,
    )
    hass.states.async_set(
        entry.entity_id,
        "12.3",
        {
            "name": name,
            "capacity": kwp * 0.8,
            "capacity_dc": kwp,
            "azimuth": 0,
            "compass_degrees": compass,
            "tilt": tilt,
        },
    )


def cloud_pack(
    hass: HomeAssistant, pack: int, cycles: float, health: float
) -> tuple[str, str]:
    registry = er.async_get(hass)
    ids = []
    for key, value in (("bpCycles", cycles), ("bpSoh", health)):
        entry = registry.async_get_or_create(
            "sensor",
            "powerocean",
            f"HJ32_PACK{pack}_JTS1_BP_STA_REPORT_{key}",
            suggested_object_id=f"pack{pack}_{key.lower()}",
        )
        hass.states.async_set(entry.entity_id, str(value))
        ids.append(entry.entity_id)
    return ids[0], ids[1]


async def test_everything_found_is_prefilled_in_the_options(
    home: HomeAssistant,
) -> None:
    home.config.latitude = LATITUDE
    set_states(home)
    solcast_site(home, "east", 7.6, 90, 25)
    solcast_site(home, "west", 7.6, 270, 25)
    cycles1, health1 = cloud_pack(home, 1, 120, 97)
    cycles2, health2 = cloud_pack(home, 2, 118, 95)
    limits = {
        key: er.async_get(home).async_get_or_create(
            "sensor", "ef_powerocean_tcpmodbus", f"x_{key}"
        )
        for key in ("battery_charge_power_limit", "battery_discharge_power_limit")
    }
    entry = await setup_entry(home)

    result = await home.config_entries.options.async_init(entry.entry_id)

    assert suggested(result, const.CONF_ARRAY_KWP) == pytest.approx(15.2)
    assert suggested(result, const.CONF_BATTERY_CYCLES) == sorted([cycles1, cycles2])
    assert suggested(result, const.CONF_BATTERY_HEALTH) == sorted([health1, health2])
    assert (
        suggested(result, const.CONF_CHARGE_POWER_LIMIT)
        == limits["battery_charge_power_limit"].entity_id
    )
    assert (
        suggested(result, const.CONF_DISCHARGE_POWER_LIMIT)
        == limits["battery_discharge_power_limit"].entity_id
    )


async def test_the_plan_uses_what_the_battery_still_holds_and_its_live_limits(
    home: HomeAssistant,
) -> None:
    set_states(home)
    _, health1 = cloud_pack(home, 1, 120, 96)
    _, health2 = cloud_pack(home, 2, 118, 94)
    home.states.async_set("sensor.charge_limit_w", "3300", {"unit_of_measurement": "W"})
    home.states.async_set(
        "sensor.discharge_limit_w", "1800", {"unit_of_measurement": "W"}
    )

    entry = await setup_entry(
        home,
        {
            const.CONF_BATTERY_HEALTH: [health1, health2],
            const.CONF_CHARGE_POWER_LIMIT: "sensor.charge_limit_w",
            const.CONF_DISCHARGE_POWER_LIMIT: "sensor.discharge_limit_w",
        },
    )
    battery = entry.runtime_data.battery()

    # 15 180 Wh nominal, at the packs' average health of 95 %.
    assert battery.capacity_kwh == pytest.approx(15.18 * 0.95, rel=0.001)
    assert battery.max_power_kw == pytest.approx(3.3)
    assert battery.discharge_kw == pytest.approx(1.8)


async def test_a_limit_the_battery_stops_reporting_falls_back_to_the_option(
    home: HomeAssistant,
) -> None:
    set_states(home)
    home.states.async_set("sensor.charge_limit_w", "unavailable")

    entry = await setup_entry(
        home,
        {
            const.CONF_CHARGE_POWER_LIMIT: "sensor.charge_limit_w",
            const.CONF_MAX_POWER: 4.2,
        },
    )

    assert entry.runtime_data.battery().max_power_kw == pytest.approx(4.2)


async def test_the_trophy_room_knows_the_array_and_where_it_faces(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    home.config.latitude = LATITUDE
    await history(home, recent(40))
    set_states(home)
    solcast_site(home, "east", 7.6, 90, 25)
    solcast_site(home, "west", 7.6, 270, 25)
    await setup_entry(home, ON)
    client = await hass_ws_client(home)

    await client.send_json_auto_id({"type": f"{const.DOMAIN}/achievements"})
    room: dict[str, Any] = (await client.receive_json())["result"]

    assert room["profile"]["kwp"] == pytest.approx(15.2)
    assert room["profile"]["relative_yield"] == pytest.approx(
        plane_yield(90, 25, LATITUDE), abs=0.01
    )
    assert "power_plant" in {b["id"] for b in room["badges"]}
