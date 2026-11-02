"""What a dashboard needs from the integration, and the script it ships."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.solar_energy_insights import const
from custom_components.solar_energy_insights.dashboard import (
    POWEROCEAN,
    SCRIPT,
    URL_BASE,
    async_register_frontend,
    string_kwp,
    string_kwp_key,
    string_panel_w_key,
    string_panels_key,
)
from tests.common import ENTITIES, recent, record_house, set_states, setup_entry
from tests.test_answers import evening_load
from tests.test_config_flow import suggested


def powerocean_strings(
    home: HomeAssistant, strings: tuple[int, ...] = (1, 3)
) -> dict[int, str]:
    """A PowerOcean installation owning the configured battery, with strings."""
    po = MockConfigEntry(domain=POWEROCEAN)
    po.add_to_hass(home)
    registry = er.async_get(home)
    registry.async_get_or_create(
        "sensor",
        POWEROCEAN,
        "abc_battery_soc",
        config_entry=po,
        suggested_object_id=ENTITIES[const.CONF_BATTERY_SOC].removeprefix("sensor."),
    )
    return {
        n: registry.async_get_or_create(
            "sensor",
            POWEROCEAN,
            f"abc_pv{n}_power",
            config_entry=po,
            suggested_object_id=f"powerocean_pv_string_{n}_power",
        ).entity_id
        for n in strings
    }


async def config_of(home: HomeAssistant, ws: WebSocketGenerator) -> dict[str, Any]:
    client = await ws(home)
    await client.send_json_auto_id({"type": f"{const.DOMAIN}/config"})
    reply = await client.receive_json()
    assert reply["success"], reply
    result: dict[str, Any] = reply["result"]
    return result


async def test_the_dashboard_gets_this_homes_settings(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    days = recent(10)
    await record_house(home, days, evening_load)
    strings = powerocean_strings(home)
    set_states(home)
    await setup_entry(home, {string_panels_key(1): 12, string_panel_w_key(1): 420})

    cfg = await config_of(home, hass_ws_client)

    assert cfg["entities"]["house"] == ENTITIES[const.CONF_HOUSE_ENERGY]
    assert cfg["entities"]["soc"] == ENTITIES[const.CONF_BATTERY_SOC]
    assert cfg["entities"]["solar"] is None  # not configured: not guessed
    assert cfg["history_start"] == days[0].isoformat()
    found = [(s["n"], s["power"], s["kwp"], s["energy"]) for s in cfg["strings"]]
    assert found == [
        (1, strings[1], 5.04, "sensor.solar_energy_insights_pv1_energy_today"),
        (3, strings[3], None, "sensor.solar_energy_insights_pv3_energy_today"),
    ]
    assert isinstance(cfg["forecasts"], dict)


async def test_no_strings_for_a_battery_that_is_not_a_powerocean(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await record_house(home, recent(3), evening_load)
    set_states(home)
    await setup_entry(home)

    assert (await config_of(home, hass_ws_client))["strings"] == []


async def test_the_options_ask_for_each_strings_panels_and_keep_them(
    home: HomeAssistant,
) -> None:
    powerocean_strings(home, (1, 2))
    set_states(home)
    entry = await setup_entry(
        home, {string_panels_key(2): 8, string_panel_w_key(2): 460}
    )

    form = await home.config_entries.options.async_init(entry.entry_id)

    assert form["data_schema"] is not None
    fields = {str(k): v for k, v in form["data_schema"].schema.items()}
    assert {string_panels_key(1), string_panel_w_key(1), string_panels_key(2)} <= set(
        fields
    )
    assert string_panels_key(3) not in fields
    # Whole numbers only: a phone shows a plain number pad, no decimal separator.
    for key in (string_panels_key(1), string_panel_w_key(1)):
        assert fields[key].config["step"] == 1
    assert suggested(form, string_panels_key(2)) == 8
    # Kept when the integration loads again, not dropped as an old option.
    await home.config_entries.async_reload(entry.entry_id)
    await home.async_block_till_done()
    assert entry.options[string_panel_w_key(2)] == 460


async def test_the_script_is_served_and_loaded_on_every_page(
    home: HomeAssistant,
) -> None:
    home.config.components.update({"frontend", "http"})
    home.http = MagicMock(async_register_static_paths=AsyncMock())
    with patch("homeassistant.components.frontend.add_extra_js_url") as add:
        await async_register_frontend(home)
        await async_register_frontend(home)  # once only

    paths = home.http.async_register_static_paths.await_args.args[0]
    assert paths[0].url_path == URL_BASE
    assert paths[0].path.endswith("frontend")
    add.assert_called_once()
    url = add.call_args.args[1]
    assert url.startswith(f"{URL_BASE}/{SCRIPT}?v=")


def test_the_shipped_script_carries_nothing_of_one_home() -> None:
    from pathlib import Path

    script = (
        Path(__file__).parent.parent
        / "custom_components/solar_energy_insights/frontend"
        / SCRIPT
    ).read_text()
    for personal in ("ecoflow_powerocean", "pv_string_", "2026, 4, 8"):
        assert personal not in script


@pytest.mark.parametrize("n", (1, 2, 3))
def test_each_string_size_has_a_plain_explanation(n: int) -> None:
    import json
    from pathlib import Path

    strings = json.loads(
        (
            Path(__file__).parent.parent
            / "custom_components/solar_energy_insights/translations/en.json"
        ).read_text()
    )["options"]["step"]["init"]
    assert (
        strings["data"][string_panels_key(n)] == f"PV{n}: number of panels (optional)"
    )
    assert (
        strings["data"][string_panel_w_key(n)] == f"PV{n}: watts per panel (optional)"
    )
    assert "for example 420" in strings["data_description"][string_panel_w_key(n)]


def test_a_size_from_panels_or_the_older_kwp_option() -> None:
    assert string_kwp({string_panels_key(1): 12, string_panel_w_key(1): 420}, 1) == 5.04
    assert string_kwp({string_kwp_key(1): 5.06}, 1) == 5.06  # set before, still counts
    assert string_kwp({string_panels_key(1): 12}, 1) is None  # half a size is no size
