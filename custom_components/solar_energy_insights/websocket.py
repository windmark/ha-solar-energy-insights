"""WebSocket command for cards: the plan with every night and the battery curve."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN
from .core.scenario import string_worth
from .dashboard import dashboard_config
from .scenario_lab import string_worth_dict
from .serialize import backtest_dict, plan_dict
from .services import MIN_SCENARIO_DAYS, coordinator_for


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_plan)
    websocket_api.async_register_command(hass, ws_backtest)
    websocket_api.async_register_command(hass, ws_achievements)
    websocket_api.async_register_command(hass, ws_config)
    websocket_api.async_register_command(hass, ws_string_worth)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/plan"})
@callback
def ws_plan(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    try:
        coordinator = coordinator_for(hass)
    except ServiceValidationError:
        connection.send_error(
            msg["id"], "not_loaded", "Solar Energy Insights is not loaded"
        )
        return
    current = coordinator.plan
    connection.send_result(
        msg["id"],
        plan_dict(current, prices=(coordinator.price, coordinator.feed_in))
        if current is not None
        else None,
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/backtest"})
@callback
def ws_backtest(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every scored evening, what the recent evenings taught, and how the plan's
    own answers did: for drawing how the plan has done."""
    try:
        coordinator = coordinator_for(hass)
    except ServiceValidationError:
        connection.send_error(
            msg["id"], "not_loaded", "Solar Energy Insights is not loaded"
        )
        return
    result = backtest_dict(coordinator.calibration) or {}
    live = coordinator.live_nights
    full = coordinator.live_full
    result["live"] = {
        "nights": None
        if live is None
        else {
            "count": live.nights,
            "error_kwh": live.error_kwh,
            "lasts_right": live.lasts_right,
        },
        "full_at": None
        if full is None
        else {
            "days": full.days,
            "error_minutes": full.error_minutes,
            "missed": full.missed,
        },
    }
    result["evenings"] = [
        {"day": e.day.isoformat(), "start_soc": e.start_soc, "lowest_soc": e.lowest_soc}
        for e in coordinator.evenings
    ]
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/achievements"})
@callback
def ws_achievements(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The whole trophy room: score, level, rank, streaks, records, badges,
    quests and monthly recaps."""
    try:
        coordinator = coordinator_for(hass)
    except ServiceValidationError:
        connection.send_error(
            msg["id"], "not_loaded", "Solar Energy Insights is not loaded"
        )
        return
    if not coordinator.achievements.enabled:
        connection.send_error(
            msg["id"], "disabled", "Achievements are off in the options"
        )
        return
    connection.send_result(msg["id"], coordinator.achievements.view)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/config"})
@websocket_api.async_response
async def ws_config(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """What a dashboard needs about this home: the configured counters, the
    first day of history, PowerOcean's strings with their sizes, and the saved
    morning forecasts."""
    try:
        coordinator = coordinator_for(hass)
    except ServiceValidationError:
        connection.send_error(
            msg["id"], "not_loaded", "Solar Energy Insights is not loaded"
        )
        return
    connection.send_result(msg["id"], await dashboard_config(coordinator))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/string_worth",
        vol.Required("string"): vol.All(int, vol.Range(min=1, max=3)),
    }
)
@websocket_api.async_response
async def ws_string_worth(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The recorded year replayed with and without one string: what it made,
    what the grid would have given and taken without it, and the meters to
    check the replay against."""
    try:
        coordinator = coordinator_for(hass)
    except ServiceValidationError:
        connection.send_error(
            msg["id"], "not_loaded", "Solar Energy Insights is not loaded"
        )
        return
    lab = coordinator.scenario_lab
    if lab.solar is None:
        connection.send_error(
            msg["id"], "needs_solar_counter", "Set the solar energy counter first"
        )
        return
    try:
        battery = coordinator.battery(as_reported=True)
    except UpdateFailed as err:
        connection.send_error(msg["id"], "not_ready", str(err))
        return
    hours = [h for h in await lab.async_hours() if h.strings]
    if len(hours) < MIN_SCENARIO_DAYS * 24:
        connection.send_error(
            msg["id"],
            "too_little_history",
            "Needs a week of string readings",
        )
        return
    result = await hass.async_add_executor_job(
        string_worth,
        hours,
        battery,
        msg["string"],
        coordinator.price,
        coordinator.feed_in,
        coordinator.export_cap_kw(),
    )
    connection.send_result(msg["id"], string_worth_dict(result))
