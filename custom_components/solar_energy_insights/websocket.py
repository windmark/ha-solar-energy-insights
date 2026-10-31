"""WebSocket command for cards: the plan with every night and the battery curve."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .serialize import backtest_dict, plan_dict
from .services import coordinator_for


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_plan)
    websocket_api.async_register_command(hass, ws_backtest)


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
