"""The solar array as Solcast knows it: each rooftop site's size and direction."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .core.array import Array, from_sites

SOLCAST = "solcast_solar"
# Solcast's rooftop site sensors: unique ids "solcast_solcast_api_<site>".
ROOFTOP_PREFIX = "solcast_solcast_api_"


def solcast_sites(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Each rooftop site's attributes: capacity, capacity_dc, azimuth, tilt."""
    sites = []
    for entry in er.async_get(hass).entities.values():
        if entry.platform != SOLCAST or not entry.unique_id.startswith(ROOFTOP_PREFIX):
            continue
        state = hass.states.get(entry.entity_id)
        if state is not None and state.attributes.get("capacity") is not None:
            sites.append(dict(state.attributes))
    return sites


def array_of(hass: HomeAssistant, kwp_override: float | None = None) -> Array:
    """The array from Solcast's sites; a size set by hand wins."""
    array = from_sites(solcast_sites(hass), hass.config.latitude)
    if kwp_override:
        return Array(array.planes, array.latitude, kwp_override)
    return array
