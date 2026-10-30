"""Actions and the WebSocket API answer from the same plan the sensors show."""

from __future__ import annotations

from datetime import timedelta
from types import MappingProxyType
from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.solar_energy_insights import const
from custom_components.solar_energy_insights.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.common import DAY, set_states, setup_entry, state
from tests.history_helpers import import_days

DRYER = ConfigSubentryData(
    data=MappingProxyType(
        {
            const.CONF_NAME: "Dryer",
            const.CONF_POWER_ENTITY: "sensor.dryer_power",
            const.CONF_DEFAULT_KWH: 2.5,
            const.CONF_DEFAULT_HOURS: 2.0,
            const.CONF_MAX_KWH: 6.0,
        }
    ),
    subentry_type=const.SUBENTRY_APPLIANCE,
    title="Dryer",
    unique_id=None,
)


async def simulate(home: HomeAssistant, **data: object) -> Any:
    return await home.services.async_call(
        const.DOMAIN, "simulate", data, blocking=True, return_response=True
    )


async def test_an_appliance_tonight_leaves_less_at_sunrise(home: HomeAssistant) -> None:
    set_states(home, soc=60)
    await setup_entry(home, subentries=[DRYER])

    alone = await simulate(home)
    with_dryer = await simulate(home, appliances=["dryer"])

    assert with_dryer["spare_kwh"] <= alone["spare_kwh"]
    assert with_dryer["load_kwh"] > alone["load_kwh"]
    assert with_dryer["points"]


async def test_a_cloudy_day_fills_less(home: HomeAssistant) -> None:
    set_states(home, soc=20)
    await setup_entry(home)

    expected = await simulate(home, until="sunset")
    cloudy = await simulate(home, until="sunset", forecast="cloudy")

    assert cloudy["pv_kwh"] < expected["pv_kwh"]
    assert cloudy["peak_pct"] <= expected["peak_pct"]


async def test_an_unknown_appliance_is_refused(home: HomeAssistant) -> None:
    set_states(home)
    await setup_entry(home, subentries=[DRYER])

    with pytest.raises(ServiceValidationError):
        await simulate(home, appliances=["Sauna"])


async def test_the_upgrade_replay_compares_sizes(home: HomeAssistant) -> None:
    await import_days(home, [DAY - timedelta(days=n) for n in range(12, 0, -1)])
    set_states(home)
    await setup_entry(home)

    response: Any = await home.services.async_call(
        const.DOMAIN,
        "value_upgrade",
        {"sizes": [10, 2]},
        blocking=True,
        return_response=True,
    )

    sizes = response["sizes"]
    assert [s["kwh"] for s in sizes] == [2, 10]
    assert sizes[1]["value_eur"] >= sizes[0]["value_eur"]
    assert response["hours"] > 0


async def test_the_plan_is_served_with_its_curve(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    set_states(home)
    await setup_entry(home)
    client = await hass_ws_client(home)

    await client.send_json_auto_id({"type": f"{const.DOMAIN}/plan"})
    reply = await client.receive_json()

    assert reply["success"]
    plan = reply["result"]
    assert plan["headline"] == state(home, "sensor.solar_energy_insights_plan")
    assert plan["curve"]


async def test_relearning_keeps_working(home: HomeAssistant) -> None:
    # Over a week, so each hour of the week is seen twice and can be scored.
    await import_days(home, [DAY - timedelta(days=n) for n in range(10, 0, -1)])
    set_states(home)
    await setup_entry(home)

    await home.services.async_call(const.DOMAIN, "relearn", {"days": 10}, blocking=True)

    error = home.states.get("sensor.solar_energy_insights_load_model_error")
    assert error is not None
    assert error.attributes["hours_scored"] > 0


async def test_diagnostics_describe_without_history(home: HomeAssistant) -> None:
    set_states(home)
    entry = await setup_entry(home, subentries=[DRYER])

    diagnostics = await async_get_config_entry_diagnostics(home, entry)

    assert diagnostics["plan"]["headline"]
    assert diagnostics["appliances"][0]["name"] == "Dryer"
    assert "records" in diagnostics["learned"]
