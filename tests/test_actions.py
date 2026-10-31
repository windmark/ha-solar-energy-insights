"""The simulate action, the WebSocket plan and diagnostics agree with the sensors."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.solar_energy_insights import const
from custom_components.solar_energy_insights.diagnostics import (
    async_get_config_entry_diagnostics,
)
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
from tests.test_answers import draining


def evening(hour: int) -> float:
    return 1.2 if 18 <= hour < 22 else 0.4


async def simulate(home: HomeAssistant, kwh: float) -> Any:
    return await home.services.async_call(
        const.DOMAIN,
        "simulate",
        {"extra_kwh": kwh, "extra_hours": 2},
        blocking=True,
        return_response=True,
    )


async def test_an_extra_load_leaves_less_at_sunrise(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening)
    freezer.move_to(local(DAY, 19))
    set_states(home, soc=70)
    await setup_entry(home)

    alone = await simulate(home, 0)
    with_dryer = await simulate(home, 3)

    assert (
        with_dryer["night"]["typical"]["spare_kwh"]
        < alone["night"]["typical"]["spare_kwh"]
    )
    assert with_dryer["night"]["lasting"] <= alone["night"]["lasting"]


async def test_the_plan_is_served_with_every_night(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await record_house(home, recent(10), evening)
    set_states(home)
    await setup_entry(home, {const.CONF_NIGHTS: 7})
    # Morning, as set by the fixture: a WebSocket login does not survive a clock jump.
    client = await hass_ws_client(home)

    await client.send_json_auto_id({"type": f"{const.DOMAIN}/plan"})
    reply = await client.receive_json()

    assert reply["success"]
    night = reply["result"]["night"]
    assert len(night["each"]) == 7
    assert str(round(night["lasting"] / 7 * 100)) == state(
        home, "sensor.solar_energy_insights_nights_covered"
    )
    assert reply["result"]["curve"]


async def test_diagnostics_say_how_much_history_is_used(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(5), evening)
    freezer.move_to(local(DAY, 19))
    set_states(home)
    entry = await setup_entry(home)

    diagnostics = await async_get_config_entry_diagnostics(home, entry)

    assert diagnostics["days_of_history"] >= 5
    assert diagnostics["plan"]["night"]["nights"] == 5


async def test_the_plan_draws_every_replayed_night(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await record_house(home, recent(10), evening)
    set_states(home)
    await setup_entry(home, {const.CONF_NIGHTS: 7})
    client = await hass_ws_client(home)

    await client.send_json_auto_id({"type": f"{const.DOMAIN}/plan"})
    night = (await client.receive_json())["result"]["night"]

    curves = night["curves"]
    assert len(curves["levels"]) == len(night["each"]) == 7
    assert all(len(row) == len(curves["times"]) for row in curves["levels"])
    assert all(0 <= v <= 100 for row in curves["levels"] for v in row)


async def test_the_backtest_is_served_for_drawing(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await record_house(home, recent(20), evening)
    await record_battery(home, recent(20), draining)
    set_states(home)
    await setup_entry(home)
    client = await hass_ws_client(home)

    await client.send_json_auto_id({"type": f"{const.DOMAIN}/backtest"})
    reply = await client.receive_json()

    assert reply["success"]
    result = reply["result"]
    assert result["nights"]
    assert {"day", "contested", "predicted_spare_kwh", "actual_spare_kwh"} <= set(
        result["nights"][0]
    )
    assert result["weighting"]
    assert "live" in result
    assert len(result["evenings"]) == len(result["nights"])
