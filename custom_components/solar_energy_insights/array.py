"""The solar array as Solcast knows it: each rooftop site's size and direction."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .core.array import Array, from_sites


def solcast_sites(hass: HomeAssistant, entity_ids: list[str]) -> list[dict[str, Any]]:
    """Each configured rooftop site's attributes: capacity, capacity_dc,
    azimuth, tilt."""
    sites = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is not None and state.attributes.get("capacity") is not None:
            sites.append(dict(state.attributes))
    return sites


def array_of(
    hass: HomeAssistant, sites: list[str], kwp_override: float | None = None
) -> Array:
    """The array from the configured Solcast sites; a size set by hand wins."""
    array = from_sites(solcast_sites(hass, sites), hass.config.latitude)
    if kwp_override:
        return Array(array.planes, array.latitude, kwp_override)
    return array
