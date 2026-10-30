"""Learning from recorded history: day records, nights, forecasts and the load."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.solar_energy_insights import const
from tests.common import DAY, local, set_states, setup_entry, state
from tests.history_helpers import house_with_dinner, import_days, sunny_solar

YESTERDAY = DAY - timedelta(days=1)


async def days(home: HomeAssistant, ws: WebSocketGenerator) -> list[dict[str, Any]]:
    client = await ws(home)
    await client.send_json_auto_id({"type": f"{const.DOMAIN}/days"})
    reply = await client.receive_json()
    assert reply["success"], reply
    return reply["result"]["days"]


async def test_a_recorded_day_becomes_a_record(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await import_days(home, [YESTERDAY])
    set_states(home)

    await setup_entry(home)

    record = next(
        d for d in await days(home, hass_ws_client) if d["day"] == YESTERDAY.isoformat()
    )
    assert record["totals"]["solar"] == pytest.approx(sum(map(sunny_solar, range(24))))
    assert record["totals"]["house"] == pytest.approx(
        sum(map(house_with_dinner, range(24)))
    )
    assert 0 < record["self_sufficiency_pct"] < 100
    assert record["flags"]["battery_full"] is True
    assert record["full_at"] is not None
    assert 0 <= record["score"]["total"] <= 100


async def test_a_night_that_ran_down_after_a_full_day_is_too_small(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await import_days(home, [YESTERDAY, DAY])
    set_states(home, reserve=10)

    await setup_entry(home)

    record = next(
        d for d in await days(home, hass_ws_client) if d["day"] == YESTERDAY.isoformat()
    )
    assert record["night"]["grid_kwh"] > 0
    assert record["flags"]["grid_free_night"] is False
    assert state(home, "sensor.solar_energy_insights_battery_sizing") == "too_small"


async def test_days_without_history_are_not_invented(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    set_states(home)

    await setup_entry(home)

    assert await days(home, hass_ws_client) == []


async def test_a_day_is_judged_against_its_morning_forecast(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local(DAY, 5, 59))
    set_states(home, peak_kw=8)
    entry = await setup_entry(home)

    freezer.move_to(local(DAY, 6))
    async_fire_time_changed(home)
    await home.async_block_till_done()
    morning = float(state(home, "sensor.solcast_pv_forecast_forecast_today"))
    # Solcast revises during the day; the record keeps the morning's number.
    set_states(home, peak_kw=3)

    await import_days(home, [DAY])
    for moment in (
        local(DAY, 12),
        local(DAY, 21),
        local(DAY, 23, 55),
        local(DAY, 24 - 1, 59),
    ):
        freezer.move_to(moment)
        async_fire_time_changed(home)
        await home.async_block_till_done()
    freezer.move_to(local(DAY, 0, 15) + timedelta(days=1))
    async_fire_time_changed(home)
    await home.async_block_till_done(wait_background_tasks=True)

    # The clock jumped a day, which a WebSocket login would not survive.
    record = entry.runtime_data.data_store.record(DAY)
    assert record is not None
    assert record.forecasts.morning == morning
    assert record.forecasts.morning_hourly


async def test_learns_the_house_from_past_weeks(home: HomeAssistant) -> None:
    await import_days(home, [DAY - timedelta(days=n) for n in range(14, 0, -1)])
    set_states(home)

    await setup_entry(home)

    error = home.states.get("sensor.solar_energy_insights_load_model_error")
    assert error is not None
    assert error.attributes["hours_scored"] > 0
    assert float(error.state) >= 0
