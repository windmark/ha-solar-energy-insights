"""Appliances added under the integration, one subentry each: what a run
takes, and how the cards see them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_ICON

from .const import (
    CONF_ENERGY_PER_RUN,
    CONF_RUN_HOURS,
    CONF_RUN_KWH,
    DEFAULT_RUN_HOURS,
    DEFAULT_RUN_KWH,
    SUBENTRY_APPLIANCE,
)
from .core.appliances import Run, learned_run

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import HomeAssistant

DEFAULT_ICON = "mdi:power-plug-outline"


def appliances(entry: ConfigEntry) -> list[ConfigSubentry]:
    return [
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_APPLIANCE
    ]


def appliance_run(hass: HomeAssistant, data: Any) -> Run:
    """The appliance's run: what its sensor learned, else the typical figures."""
    kwh = float(data.get(CONF_RUN_KWH, DEFAULT_RUN_KWH))
    hours = float(data.get(CONF_RUN_HOURS, DEFAULT_RUN_HOURS))
    entity_id = data.get(CONF_ENERGY_PER_RUN)
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return learned_run(kwh, hours)
    try:
        learned: float | None = float(state.state)
    except ValueError:
        learned = None
    durations = state.attributes.get("hours")
    return learned_run(
        kwh,
        hours,
        learned,
        [float(h) for h in durations] if isinstance(durations, list) else None,
    )


def card_appliances(entry: ConfigEntry) -> list[dict[str, Any]]:
    """The appliances as the cards take them in their own settings."""
    return [
        {
            "name": s.title,
            "icon": s.data.get(CONF_ICON) or DEFAULT_ICON,
            "kwh": s.data.get(CONF_RUN_KWH, DEFAULT_RUN_KWH),
            "hours": s.data.get(CONF_RUN_HOURS, DEFAULT_RUN_HOURS),
            **(
                {"energy_per_run": s.data[CONF_ENERGY_PER_RUN]}
                if s.data.get(CONF_ENERGY_PER_RUN)
                else {}
            ),
        }
        for s in appliances(entry)
    ]
