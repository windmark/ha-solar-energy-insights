"""Appliances added under the integration, one subentry each: what a run
takes, and how the cards see them."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_ICON

from .const import (
    CONF_APPLIANCE_ENERGY,
    CONF_RUN_HOURS,
    CONF_RUN_KWH,
    DEFAULT_RUN_HOURS,
    DEFAULT_RUN_KWH,
    SUBENTRY_APPLIANCE,
)
from .core.appliances import Run, run_from

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigSubentry

DEFAULT_ICON = "mdi:power-plug-outline"


def appliances(entry: ConfigEntry) -> list[ConfigSubentry]:
    return [
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_APPLIANCE
    ]


def appliance_run(data: Any, runs: list[tuple[float, float]] | None) -> Run:
    """The appliance's run: learned from its counter's recent runs, else the
    typical figures."""
    kwh = float(data.get(CONF_RUN_KWH, DEFAULT_RUN_KWH))
    hours = float(data.get(CONF_RUN_HOURS, DEFAULT_RUN_HOURS))
    return run_from(kwh, hours, runs or [])


def card_appliances(
    entry: ConfigEntry, runs: Mapping[str, list[tuple[float, float]]] | None = None
) -> list[dict[str, Any]]:
    """The appliances as the cards take them: the run as learned from each
    counter, and whether it was learned or is still the typical figures."""
    out = []
    for s in appliances(entry):
        counter = s.data.get(CONF_APPLIANCE_ENERGY)
        run = appliance_run(s.data, (runs or {}).get(counter) if counter else None)
        out.append(
            {
                "name": s.title,
                "icon": s.data.get(CONF_ICON) or DEFAULT_ICON,
                "kwh": round(run.kwh, 2),
                "hours": round(run.hours, 2),
                "learned": not run.estimated,
                **({"energy": counter} if counter else {}),
            }
        )
    return out


def appliance_energies(entry: ConfigEntry) -> list[str]:
    """The appliances' own energy counters, where set."""
    return [
        s.data[CONF_APPLIANCE_ENERGY]
        for s in appliances(entry)
        if s.data.get(CONF_APPLIANCE_ENERGY)
    ]
