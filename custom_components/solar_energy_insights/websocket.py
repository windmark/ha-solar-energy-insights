"""WebSocket commands for cards: day records, the full plan, the upgrade replay.

Cards ask here instead of querying the recorder themselves, so every device shows
the same numbers.
"""

from __future__ import annotations

from datetime import date
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
    websocket_api.async_register_command(hass, ws_days)
    websocket_api.async_register_command(hass, ws_plan)
    websocket_api.async_register_command(hass, ws_upgrade)


def _coordinator(
    connection: websocket_api.ActiveConnection, msg: dict[str, Any], hass: HomeAssistant
) -> Any:
    try:
        return coordinator_for(hass)
    except ServiceValidationError:
        connection.send_error(
            msg["id"], "not_loaded", "Solar Energy Insights is not loaded"
        )
        return None


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/days",
        vol.Optional("start"): str,
        vol.Optional("end"): str,
    }
)
@callback
def ws_days(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Day records, oldest first: totals, score, forecasts, night and flags."""
    c = _coordinator(connection, msg, hass)
    if c is None:
        return
    try:
        start = date.fromisoformat(msg["start"]) if "start" in msg else date.min
        end = date.fromisoformat(msg["end"]) if "end" in msg else date.max
    except ValueError:
        connection.send_error(msg["id"], "invalid_format", "Dates are YYYY-MM-DD")
        return
    records = [
        record.as_dict()
        for _, record in sorted(c.data_store.records.items())
        if start <= record.day <= end
    ]
    connection.send_result(msg["id"], {"days": records})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/plan"})
@callback
def ws_plan(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The current plan with its battery curve."""
    c = _coordinator(connection, msg, hass)
    if c is None:
        return
    plan = c.plan
    connection.send_result(msg["id"], plan_dict(plan) if plan is not None else None)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/upgrade",
        vol.Optional("sizes", default=[2, 4, 6, 8, 10, 12, 16]): [
            vol.All(vol.Coerce(float), vol.Range(min=0.5, max=200))
        ],
    }
)
@websocket_api.async_response
async def ws_upgrade(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """What added capacity would have been worth, per size, replayed from history."""
    c = _coordinator(connection, msg, hass)
    if c is None:
        return
    connection.send_result(
        msg["id"], await c.learner.async_upgrade(sorted(set(msg["sizes"])))
    )
