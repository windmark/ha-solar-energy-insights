"""Appliances found among the energy counters, to suggest rather than add.

A counter whose name, or its device's, reads like an appliance (a dishwasher's
plug, a dryer) is a suggestion: the appliance form offers it, prefilled, and a
repair offers to add it or to give an appliance of the same name its counter.
Nothing is added without the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .appliance import appliances
from .const import (
    CONF_APPLIANCE_ENERGY,
    CONF_ENERGY_PER_RUN,
    CONF_REJECTED_APPLIANCES,
    CONF_RUN_HOURS,
    CONF_RUN_KWH,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


@dataclass(frozen=True, slots=True)
class Kind:
    name: str
    icon: str
    kwh: float
    hours: float
    words: tuple[str, ...]


# The first kind whose word appears wins: "washer dryer" is a dryer.
KINDS = (
    Kind(
        "Dishwasher",
        "mdi:dishwasher",
        1.1,
        2.5,
        (
            "dishwasher",
            "spuelmaschine",
            "spulmaschine",
            "geschirrspuel",
            "geschirrspul",
        ),
    ),
    Kind(
        "Dryer",
        "mdi:tumble-dryer",
        1.5,
        2.0,
        ("dryer", "trockner"),
    ),
    Kind(
        "Washing machine",
        "mdi:washing-machine",
        0.8,
        2.0,
        ("washing", "washer", "waschmaschine"),
    ),
    Kind("Oven", "mdi:stove", 1.0, 1.0, ("oven", "backofen", "herd")),
)
# Sensors on the same device that learn the energy of a run.
PER_RUN_WORDS = ("per_run", "last_run", "last_cycle", "cycle_energy", "run_energy")
# Daily or period meters restart: a counter of all time is what is wanted.
PERIOD_WORDS = ("today", "daily", "yesterday", "week", "month", "year")
# Worked out rather than measured: an estimate is not what the appliance used.
DERIVED_WORDS = (
    "estimate",
    "estimated",
    "forecast",
    "predicted",
    "projected",
    "expected",
    "cost",
    "per_run",
    "last_run",
    "last_cycle",
    "cycle_energy",
    "run_energy",
)
# Words that mark the meter on the appliance's own socket or circuit.
MEASURED_WORDS = ("plug", "socket", "steckdose", "meter", "switch")


@dataclass(frozen=True, slots=True)
class Found:
    """An appliance counter not yet in use."""

    energy: str
    name: str
    icon: str
    kwh: float
    hours: float
    energy_per_run: str | None = None
    # An appliance of the same name that has no counter yet.
    existing: str | None = None

    def data(self) -> dict[str, Any]:
        """The appliance's settings, as the form would save them."""
        return {
            "name": self.name,
            "icon": self.icon,
            CONF_RUN_KWH: self.kwh,
            CONF_RUN_HOURS: self.hours,
            CONF_APPLIANCE_ENERGY: self.energy,
            **(
                {CONF_ENERGY_PER_RUN: self.energy_per_run}
                if self.energy_per_run
                else {}
            ),
        }


def _plain(text: str) -> str:
    return (
        text.lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def _kind(text: str) -> Kind | None:
    plain = _plain(text)
    return next((k for k in KINDS if any(w in plain for w in k.words)), None)


def _is_energy_counter(hass: HomeAssistant, e: er.RegistryEntry) -> bool:
    if e.domain != "sensor" or e.platform == DOMAIN or e.disabled_by is not None:
        return False
    state = hass.states.get(e.entity_id)
    attrs = state.attributes if state is not None else {}
    device_class = (
        e.device_class or e.original_device_class or attrs.get("device_class")
    )
    if device_class != "energy":
        return False
    if attrs.get("state_class") not in (None, "total", "total_increasing"):
        return False
    text = _plain(" ".join((e.entity_id, e.name or "", e.original_name or "")))
    return not any(w in text for w in PERIOD_WORDS + DERIVED_WORDS)


def _rank(e: er.RegistryEntry) -> tuple[int, int, str]:
    """Better first: a socket's own meter, then the shortest name."""
    measured = any(w in e.entity_id for w in MEASURED_WORDS)
    return (0 if measured else 1, len(e.entity_id), e.entity_id)


def found_appliances(hass: HomeAssistant, entry: ConfigEntry) -> list[Found]:
    """One counter per appliance not yet given one: an appliance seen through
    several devices or integrations (its plug, a learning integration) is
    still one appliance. Counters turned down are not suggested again."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    used = {
        s.data.get(CONF_APPLIANCE_ENERGY)
        for s in appliances(entry)
        if s.data.get(CONF_APPLIANCE_ENERGY)
    }
    without_counter = {
        s.title.strip().lower(): s.subentry_id
        for s in appliances(entry)
        if not s.data.get(CONF_APPLIANCE_ENERGY)
    }
    # An appliance that has a counter already, by its kind and by its name.
    counted = {
        k.name
        for s in appliances(entry)
        if s.data.get(CONF_APPLIANCE_ENERGY)
        and (k := _kind(s.title) or _kind(str(s.data.get(CONF_APPLIANCE_ENERGY))))
    }
    rejected = set(entry.options.get(CONF_REJECTED_APPLIANCES) or [])
    out: dict[str, Found] = {}
    candidates = sorted(
        (
            e
            for e in entities.entities.values()
            if e.entity_id not in used
            and e.entity_id not in rejected
            and _is_energy_counter(hass, e)
        ),
        key=_rank,
    )
    for e in candidates:
        device = devices.async_get(e.device_id) if e.device_id else None
        device_name = (device.name_by_user or device.name or "") if device else ""
        kind = _kind(
            " ".join((e.entity_id, e.name or "", e.original_name or "", device_name))
        )
        if kind is None or kind.name in counted or kind.name in out:
            continue
        group = kind.name
        per_run = (
            next(
                (
                    o.entity_id
                    for o in er.async_entries_for_device(entities, e.device_id)
                    if o.domain == "sensor"
                    and o.entity_id != e.entity_id
                    and any(w in o.entity_id for w in PER_RUN_WORDS)
                ),
                None,
            )
            if e.device_id
            else None
        )
        name = kind.name
        out[group] = Found(
            energy=e.entity_id,
            name=name,
            icon=kind.icon,
            kwh=kind.kwh,
            hours=kind.hours,
            energy_per_run=per_run,
            existing=without_counter.get(name.lower()),
        )
    return sorted(out.values(), key=lambda f: f.energy)


def issue_id(found: Found) -> str:
    return "appliance_found_" + re.sub(r"[^a-z0-9_]", "_", found.energy)
