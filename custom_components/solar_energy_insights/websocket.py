"""WebSocket command for cards: the plan with every night and the battery curve."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .serialize import plan_dict
from .services import coordinator_for


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_plan)


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
