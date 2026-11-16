"""What a dashboard needs from the integration, and the script it ships."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .appliance import card_appliances
from .const import (
    CONF_BATTERY_CHARGED_ENERGY,
    CONF_BATTERY_DISCHARGED_ENERGY,
    CONF_BATTERY_SOC,
    CONF_GRID_EXPORT_ENERGY,
    CONF_GRID_IMPORT_ENERGY,
    CONF_GRID_POWER,
    CONF_HOUSE_ENERGY,
    CONF_SOLAR_ENERGY,
    CONF_SOLAR_POWER,
    CONF_SOLCAST_DAYS,
    CONF_SOLCAST_SITES,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    DOMAIN,
)
from .discovery import STRINGS, string_power_key

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import InsightsCoordinator

# Panels and watts per panel: whole numbers anyone can read off a panel, and no
# decimals, which some phone keypads can't enter. The kWp option is older.
STRING_PANELS = "string_panels_pv{}"
STRING_PANEL_W = "string_panel_w_pv{}"
STRING_KWP = "string_kwp_pv{}"
STRING_ROOF = "string_roof_pv{}"
# The user's own choices of which strings to hold against which, in one section
# of the options: nothing about them is guessed from the strings' readings.
COMPARISONS = "comparisons"
COMPARE_STRING = "compare_string"
COMPARE_AGAINST = "compare_against"
COMPARE_NAME = "compare_name"
MATCHED_STRINGS = "matched_strings"
WORDS = (
    "north",
    "north-east",
    "east",
    "south-east",
    "south",
    "south-west",
    "west",
    "north-west",
)
URL_BASE = f"/{DOMAIN}_static"
SCRIPT = "solar-energy-insights.js"
_REGISTERED = f"{DOMAIN}_frontend"


def string_kwp_key(n: int) -> str:
    """The older option holding string *n*'s size in kWp."""
    return STRING_KWP.format(n)


def string_panels_key(n: int) -> str:
    return STRING_PANELS.format(n)


def string_panel_w_key(n: int) -> str:
    return STRING_PANEL_W.format(n)


def string_kwp(options: Mapping[str, Any], n: int) -> float | None:
    """String *n*'s size in kWp: panels times watts each, divided by 1000, or the older
    kWp option; None until it is known."""
    panels, watts = (
        options.get(string_panels_key(n)),
        options.get(string_panel_w_key(n)),
    )
    if panels and watts:
        return round(float(panels) * float(watts) / 1000, 3)
    older = options.get(string_kwp_key(n))
    return float(older) if older else None


def string_roof_key(n: int) -> str:
    """The option holding which Solcast site (roof) string *n* is on."""
    return STRING_ROOF.format(n)


def _numbers(values: Any, known: list[int]) -> list[int]:
    out = []
    for v in values or []:
        try:
            n = int(v)
        except TypeError, ValueError:
            continue
        if n in known and n not in out:
            out.append(n)
    return sorted(out)


def comparisons(options: Mapping[str, Any], known: list[int]) -> dict[str, Any]:
    """The strings the user singled out and matched, limited to strings that
    exist: a string held against the others, and strings that should make alike."""
    chosen = options.get(COMPARISONS) or {}
    single = _numbers([chosen.get(COMPARE_STRING)], known)
    single_out = None
    if single:
        n = single[0]
        against = [m for m in _numbers(chosen.get(COMPARE_AGAINST), known) if m != n]
        against = against or [m for m in known if m != n]
        if against:
            single_out = {
                "string": n,
                "against": against,
                "name": str(chosen.get(COMPARE_NAME) or "").strip() or f"PV{n}",
            }
    matched = _numbers(chosen.get(MATCHED_STRINGS), known)
    return {"single": single_out, "matched": matched if len(matched) >= 2 else []}


def facing(compass: float) -> str:
    """A compass direction in words: 172° is south."""
    return WORDS[round(compass / 45) % 8]


def roofs(hass: HomeAssistant, sites: list[str]) -> list[dict[str, Any]]:
    """The roofs of these Solcast sites: each one's name, direction, tilt and size."""
    from .array import array_of

    return [
        {
            "name": p.name or f"Roof {k + 1}",
            "compass": p.compass_deg,
            "tilt": p.tilt_deg,
            "kwp": p.kwp,
            "facing": facing(p.compass_deg),
        }
        for k, p in enumerate(array_of(hass, sites).planes)
    ]


def configured_strings(entry: ConfigEntry) -> list[dict[str, Any]]:
    """The strings whose power sensor is set in the options:
    [{"n": 1, "name": "PV1", "power": entity_id}, …]."""
    return [
        {"n": n, "name": f"PV{n}", "power": power}
        for n in STRINGS
        if (power := entry.options.get(string_power_key(n)))
    ]


def _prefix(entity_ids: list[str]) -> str:
    """What the configured counters' names share, up to an underscore
    (sensor.ecoflow_powerocean_), for cards that build further names from it."""
    if len(entity_ids) < 2:
        return ""
    shortest = min(entity_ids, key=len)
    length = next(
        (k for k, ch in enumerate(shortest) if any(e[k] != ch for e in entity_ids)),
        len(shortest),
    )
    common = shortest[:length]
    return common[: common.rfind("_") + 1] if "_" in common else ""


async def history_start(hass: HomeAssistant, entity_id: str) -> date | None:
    """The first day the recorder has statistics for this counter: its first
    month from the monthly statistics, then the day from that month's daily ones."""

    async def first(start: datetime, end: datetime, period: str) -> datetime | None:
        rows = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start,
            end,
            {entity_id},
            period,
            None,
            {"change"},
        )
        found = rows.get(entity_id) or []
        return dt_util.utc_from_timestamp(found[0]["start"]) if found else None

    now = dt_util.utcnow()
    month = await first(datetime(2000, 1, 1, tzinfo=UTC), now, "month")
    if month is None:
        return None
    day = await first(month, min(month + timedelta(days=32), now), "day")
    return dt_util.as_local(day or month).date()


async def dashboard_config(c: InsightsCoordinator) -> dict[str, Any]:
    """Everything the shipped script needs about this home."""
    entry = c.config_entry
    setting = {**entry.data, **entry.options}
    entities = {
        "solar": setting.get(CONF_SOLAR_ENERGY),
        "house": setting.get(CONF_HOUSE_ENERGY),
        "import": setting.get(CONF_GRID_IMPORT_ENERGY),
        "export": setting.get(CONF_GRID_EXPORT_ENERGY),
        "soc": setting.get(CONF_BATTERY_SOC),
        "solar_power": setting.get(CONF_SOLAR_POWER),
        "grid_power": setting.get(CONF_GRID_POWER),
        "battery_charged": setting.get(CONF_BATTERY_CHARGED_ENERGY),
        "battery_discharged": setting.get(CONF_BATTERY_DISCHARGED_ENERGY),
        "solcast_today": setting.get(CONF_SOLCAST_TODAY),
        "solcast_tomorrow": setting.get(CONF_SOLCAST_TOMORROW),
        # Day 3 onwards, in order.
        "solcast_days": list(setting.get(CONF_SOLCAST_DAYS) or []),
    }
    start = (
        await history_start(c.hass, str(entities["house"]))
        if entities["house"]
        else None
    )
    by_name = {
        r["name"]: r for r in roofs(c.hass, setting.get(CONF_SOLCAST_SITES) or [])
    }
    strings = [
        {
            **s,
            "kwp": string_kwp(entry.options, s["n"]),
            "roof": by_name.get(str(entry.options.get(string_roof_key(s["n"]), ""))),
            "energy": f"sensor.{DOMAIN}_pv{s['n']}_energy_today",
            "power_per_kwp": f"sensor.{DOMAIN}_pv{s['n']}_power_per_kwp",
            "yield": f"sensor.{DOMAIN}_pv{s['n']}_yield_today",
        }
        for s in configured_strings(entry)
    ]
    return {
        "entities": entities,
        # Kept for dashboards written before every sensor was in the settings.
        "prefix": _prefix(
            [
                e
                for k in ("solar", "house", "import", "export", "soc", "solar_power")
                if isinstance(e := entities.get(k), str) and e
            ]
        ),
        "history_start": start.isoformat() if start else None,
        "strings": strings,
        "comparisons": comparisons(entry.options, [s["n"] for s in strings]),
        "roofs": list(by_name.values()),
        "forecasts": {
            day: [[slot[0], slot[1]] for slot in slots]
            for day, slots in sorted(c.memory.memory.forecasts.items())
        },
        # For cards without appliances of their own.
        "appliances": card_appliances(entry),
        # The solar power the plan's solar_from is the time of.
        "solar_from_kw": c.solar_from_kw,
    }


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the script and load it on every page, once."""
    loaded = hass.config.components
    if hass.data.get(_REGISTERED) or "frontend" not in loaded or "http" not in loaded:
        return
    from homeassistant.components.frontend import add_extra_js_url

    hass.data[_REGISTERED] = True
    # Fingerprinted, not versioned: the integration's version doesn't change
    # between updates, so browsers would keep an old copy.
    script = Path(__file__).parent / "frontend" / SCRIPT
    content = await hass.async_add_executor_job(script.read_bytes)
    version = hashlib.sha256(content).hexdigest()[:12]
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                URL_BASE, str(Path(__file__).parent / "frontend"), cache_headers=True
            )
        ]
    )
    add_extra_js_url(hass, f"{URL_BASE}/{SCRIPT}?v={version}")
