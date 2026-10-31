"""The array, read from Solcast's rooftop sites."""

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


async def test_the_array_is_prefilled_from_solcasts_sites(home: HomeAssistant) -> None:
    home.config.latitude = LATITUDE
    set_states(home)
    solcast_site(home, "east", 7.6, 90, 25)
    solcast_site(home, "west", 7.6, 270, 25)
    entry = await setup_entry(home)

    result = await home.config_entries.options.async_init(entry.entry_id)

    assert suggested(result, const.CONF_ARRAY_KWP) == pytest.approx(15.2)


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
