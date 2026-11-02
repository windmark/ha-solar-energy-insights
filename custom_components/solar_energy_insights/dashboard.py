"""What a dashboard needs from the integration, and the script it ships.

The dashboard's JavaScript used to carry this home's entity names, history start
and string sizes. Here they come from the integration's own settings instead:
the counters it is configured with, the first day the recorder has, and
PowerOcean's per-string power sensors, found on the same installation as the
battery. The script itself ships with the integration and is loaded on every
page, so nothing goes in /config/www.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_SOC,
    CONF_GRID_EXPORT_ENERGY,
    CONF_GRID_IMPORT_ENERGY,
    CONF_HOUSE_ENERGY,
    CONF_SOLAR_ENERGY,
    CONF_SOLAR_POWER,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import InsightsCoordinator

POWEROCEAN = "ef_powerocean_tcpmodbus"
# PowerOcean reports up to three strings: pv1_power, pv2_power, pv3_power.
STRINGS = (1, 2, 3)
STRING_KWP = "string_kwp_pv{}"
STRING_ROOF = "string_roof_pv{}"
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
    """The option holding string *n*'s size in kWp."""
    return STRING_KWP.format(n)


def string_roof_key(n: int) -> str:
    """The option holding which Solcast site (roof) string *n* is on."""
    return STRING_ROOF.format(n)


def facing(compass: float) -> str:
    """A compass direction in words: 172° is south."""
    return WORDS[round(compass / 45) % 8]


def roofs(hass: HomeAssistant) -> list[dict[str, Any]]:
    """The roofs Solcast knows: each site's name, direction, tilt and size."""
    from .array import array_of

    return [
        {
            "name": p.name or f"Roof {k + 1}",
            "compass": p.compass_deg,
            "tilt": p.tilt_deg,
            "kwp": p.kwp,
            "facing": facing(p.compass_deg),
        }
        for k, p in enumerate(array_of(hass).planes)
    ]


def discover_strings(hass: HomeAssistant, entry: ConfigEntry) -> list[dict[str, Any]]:
    """PowerOcean's per-string power sensors, on the same installation as the
    configured battery: [{"n": 1, "name": "PV1", "power": entity_id}, …]."""
    registry = er.async_get(hass)
    battery = registry.async_get(str(entry.data.get(CONF_BATTERY_SOC, "")))
    if battery is None or battery.platform != POWEROCEAN:
        return []
    found = []
    for n in STRINGS:
        for candidate in registry.entities.values():
            if (
                candidate.platform == POWEROCEAN
                and candidate.config_entry_id == battery.config_entry_id
                and candidate.unique_id.endswith(f"_pv{n}_power")
            ):
                found.append({"n": n, "name": f"PV{n}", "power": candidate.entity_id})
                break
    return found


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
    }
    start = (
        await history_start(c.hass, str(entities["house"]))
        if entities["house"]
        else None
    )
    by_name = {r["name"]: r for r in roofs(c.hass)}
    strings = [
        {
            **s,
            "kwp": entry.options.get(string_kwp_key(s["n"])),
            "roof": by_name.get(str(entry.options.get(string_roof_key(s["n"]), ""))),
            "energy": f"sensor.{DOMAIN}_pv{s['n']}_energy_today",
            "power_per_kwp": f"sensor.{DOMAIN}_pv{s['n']}_power_per_kwp",
            "yield": f"sensor.{DOMAIN}_pv{s['n']}_yield_today",
        }
        for s in discover_strings(c.hass, entry)
    ]
    return {
        "entities": entities,
        "prefix": _prefix([e for e in entities.values() if e]),
        "history_start": start.isoformat() if start else None,
        "strings": strings,
        "roofs": list(by_name.values()),
        "forecasts": {
            day: [[slot[0], slot[1]] for slot in slots]
            for day, slots in sorted(c.memory.memory.forecasts.items())
        },
    }


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the script and load it on every page, once."""
    loaded = hass.config.components
    if hass.data.get(_REGISTERED) or "frontend" not in loaded or "http" not in loaded:
        return
    from homeassistant.components.frontend import add_extra_js_url

    hass.data[_REGISTERED] = True
    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                URL_BASE, str(Path(__file__).parent / "frontend"), cache_headers=True
            )
        ]
    )
    # Versioned, so an update is not stuck behind a cached copy.
    add_extra_js_url(hass, f"{URL_BASE}/{SCRIPT}?v={version}")
