"""Suggestions for the options from other integrations' entities.

Only the options form and the first setup use these: each suggestion is saved as
an option the user sees and can change, and everything else reads the options.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_BATTERY_CHARGED_ENERGY,
    CONF_BATTERY_DISCHARGED_ENERGY,
    CONF_BATTERY_SOC,
    CONF_FEED_IN_CAP,
    CONF_GRID_EXPORT_ENERGY,
    CONF_GRID_IMPORT_ENERGY,
    CONF_SOLAR_ENERGY,
    CONF_SOLAR_POWER,
    CONF_SOLCAST_DAYS,
    CONF_SOLCAST_POLLED,
    CONF_SOLCAST_SITES,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

POWEROCEAN = "ef_powerocean_tcpmodbus"
SOLCAST = "solcast_solar"
STRINGS = (1, 2, 3)
# Solcast's rooftop site sensors: unique ids "solcast_solcast_api_<site>".
ROOFTOP_PREFIX = "solcast_solcast_api_"
# PowerOcean unique ids end in _<key>; preferred first.
POWEROCEAN_OPTION_KEYS: dict[str, tuple[str, ...]] = {
    CONF_SOLAR_ENERGY: ("solar_total",),
    CONF_GRID_IMPORT_ENERGY: ("grid_import_total",),
    CONF_GRID_EXPORT_ENERGY: ("grid_export_total",),
    CONF_BATTERY_CHARGED_ENERGY: ("bat_charged_total",),
    CONF_BATTERY_DISCHARGED_ENERGY: ("bat_discharged_total",),
    CONF_SOLAR_POWER: ("solar_power",),
    # The cap in force, which is where the inverter curtails.
    CONF_FEED_IN_CAP: ("feed_in_power_max",),
}


def string_power_key(n: int) -> str:
    return f"string_power_pv{n}"


def _entries(hass: HomeAssistant, platform: str) -> Iterable[er.RegistryEntry]:
    return (e for e in er.async_get(hass).entities.values() if e.platform == platform)


def powerocean(
    hass: HomeAssistant,
    keys: dict[str, tuple[str, ...]],
    near: str | None = None,
) -> dict[str, str]:
    """PowerOcean entities for these fields, by unique id; with `near`, only
    from the installation that entity belongs to."""
    registry = er.async_get(hass)
    anchor = registry.async_get(near) if near else None
    found: dict[str, str] = {}
    for entry in _entries(hass, POWEROCEAN):
        if entry.domain not in ("sensor", "number"):
            continue
        if anchor is not None and entry.config_entry_id != anchor.config_entry_id:
            continue
        for options in keys.values():
            for key in options:
                if entry.unique_id.endswith(f"_{key}"):
                    found[key] = entry.entity_id
    return {
        field: next(found[key] for key in options if key in found)
        for field, options in keys.items()
        if any(key in found for key in options)
    }


def string_power(hass: HomeAssistant, battery: str | None) -> dict[str, str]:
    """PowerOcean's per-string power sensors, on the battery's installation."""
    return powerocean(
        hass, {string_power_key(n): (f"pv{n}_power",) for n in STRINGS}, battery
    )


def solcast_sites(hass: HomeAssistant) -> list[str]:
    return sorted(
        e.entity_id
        for e in _entries(hass, SOLCAST)
        if e.unique_id.startswith(ROOFTOP_PREFIX)
    )


def solcast_polled(hass: HomeAssistant) -> str | None:
    return next(
        (
            e.entity_id
            for e in _entries(hass, SOLCAST)
            if e.unique_id.endswith(("last_updated", "lastupdated"))
        ),
        None,
    )


def solcast_days(hass: HomeAssistant) -> list[str]:
    """Solcast's forecasts for day 3 to day 7, in order."""
    by_day = {
        k: e.entity_id
        for e in _entries(hass, SOLCAST)
        for k in range(3, 8)
        if e.entity_id.endswith(f"_day_{k}")
    }
    return [by_day[k] for k in sorted(by_day)]


def suggestions(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Every option discovery can fill, from what is installed."""
    out: dict[str, Any] = {
        **powerocean(hass, POWEROCEAN_OPTION_KEYS),
        **string_power(hass, entry.data.get(CONF_BATTERY_SOC)),
    }
    if sites := solcast_sites(hass):
        out[CONF_SOLCAST_SITES] = sites
    if polled := solcast_polled(hass):
        out[CONF_SOLCAST_POLLED] = polled
    if days := solcast_days(hass):
        out[CONF_SOLCAST_DAYS] = days
    return out
