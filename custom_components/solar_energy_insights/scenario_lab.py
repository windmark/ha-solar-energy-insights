"""The scenario action: the last year replayed with a bigger battery or a heat
pump, from the recorder's hourly statistics."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.util import dt as dt_util

from .const import (
    CONF_GRID_EXPORT_ENERGY,
    CONF_GRID_IMPORT_ENERGY,
    CONF_HOUSE_ENERGY,
    CONF_OUTDOOR_TEMPERATURE,
    CONF_SOLAR_ENERGY,
)
from .core.nights import plausible
from .core.scenario import Comparison, Hour, Outcome, StringWorth

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import InsightsCoordinator

SCENARIO_DAYS = 365
# The year's hours are read once in a while, not on every question.
REREAD_AFTER = timedelta(hours=6)
UNITS = {
    "energy": UnitOfEnergy.KILO_WATT_HOUR,
    # An hour's mean in kW is the hour's kWh.
    "power": UnitOfPower.KILO_WATT,
    "temperature": UnitOfTemperature.CELSIUS,
}


async def recorded_hours(
    hass: HomeAssistant,
    house: str,
    solar: str,
    temperature: str | None,
    now: datetime,
    *,
    strings: Mapping[int, str] | None = None,
    grid_import: str | None = None,
    grid_export: str | None = None,
) -> list[Hour]:
    """The hours of the last year with both the house's use and the solar, with
    each string's energy (from its power) and the grid meters where recorded."""
    end = now.replace(minute=0, second=0, microsecond=0)
    strings = strings or {}
    meters = {m for m in (grid_import, grid_export) if m}
    ids = (
        {house, solar}
        | ({temperature} if temperature else set())
        | set(strings.values())
        | meters
    )
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        end - timedelta(days=SCENARIO_DAYS),
        end,
        ids,
        "hour",
        UNITS,
        {"change", "mean"},
    )

    def changes(entity_id: str) -> dict[datetime, float]:
        return plausible(
            {
                dt_util.utc_from_timestamp(row["start"]): change
                for row in rows.get(entity_id, [])
                if (change := row.get("change")) is not None
            }
        )

    def means(entity_id: str) -> dict[datetime, float]:
        return {
            dt_util.utc_from_timestamp(row["start"]): mean
            for row in rows.get(entity_id, [])
            if (mean := row.get("mean")) is not None
        }

    used, made = changes(house), changes(solar)
    temps = means(temperature) if temperature else {}
    power = {n: means(entity_id) for n, entity_id in strings.items()}
    bought = changes(grid_import) if grid_import else {}
    sold = changes(grid_export) if grid_export else {}

    def per_string(t: datetime) -> tuple[tuple[int, float], ...]:
        # Only an hour every string has a reading for: a missing one would
        # shift the shares.
        if not power or any(t not in p for p in power.values()):
            return ()
        return tuple((n, max(p[t], 0.0)) for n, p in sorted(power.items()))

    return [
        Hour(
            t,
            made[t],
            used[t],
            temps.get(t),
            per_string(t),
            bought.get(t) if grid_import else None,
            sold.get(t) if grid_export else None,
        )
        for t in sorted(used)
        if t in made
    ]


class ScenarioLab:
    """The year's hours, kept for a while: every question replays the same."""

    def __init__(self, coordinator: InsightsCoordinator) -> None:
        self.coordinator = coordinator
        self._hours: list[Hour] = []
        self._read_at: datetime | None = None

    @property
    def temperature(self) -> str | None:
        value = self.coordinator.config_entry.options.get(CONF_OUTDOOR_TEMPERATURE)
        return str(value) if value else None

    @property
    def solar(self) -> str | None:
        value = self.coordinator.config_entry.options.get(CONF_SOLAR_ENERGY)
        return str(value) if value else None

    async def async_hours(self) -> list[Hour]:
        now = dt_util.utcnow()
        solar = self.solar
        if solar is None:
            return []
        if self._read_at is None or now - self._read_at >= REREAD_AFTER:
            from .dashboard import discover_strings

            entry = self.coordinator.config_entry
            self._hours = await recorded_hours(
                self.coordinator.hass,
                entry.data[CONF_HOUSE_ENERGY],
                solar,
                self.temperature,
                now,
                strings={
                    s["n"]: s["power"]
                    for s in discover_strings(self.coordinator.hass, entry)
                },
                grid_import=entry.options.get(CONF_GRID_IMPORT_ENERGY),
                grid_export=entry.options.get(CONF_GRID_EXPORT_ENERGY),
            )
            self._read_at = now
        return self._hours


def _outcome(o: Outcome, per_year: float) -> dict[str, Any]:
    return {
        "import_kwh": round(o.import_kwh * per_year),
        "export_kwh": round(o.export_kwh * per_year),
        "use_kwh": round(o.load_kwh * per_year),
        "heat_pump_kwh": round(o.heat_pump_kwh * per_year),
        "self_sufficiency_pct": round(o.self_sufficiency * 100, 1),
        "nights": o.nights,
        "nights_off_grid": o.nights_off_grid,
    }


def comparison_dict(
    c: Comparison, extra_battery_kwh: float, battery_price: float | None
) -> dict[str, Any]:
    """The comparison per year, with the battery's payback if it has a price."""
    per_year = c.per_year
    saving = c.saving_per_year
    share = c.heat_pump_solar_share
    return {
        "days": round(c.now.days, 1),
        "now": _outcome(c.now, per_year),
        "changed": _outcome(c.changed, per_year),
        "saving_per_year": round(saving, 2),
        "heat_pump_solar_pct": None if share is None else round(share * 100, 1),
        "payback_years": round(battery_price / saving, 1)
        if battery_price and extra_battery_kwh > 0 and saving > 0
        else None,
        "price": round(c.price, 4),
        "feed_in": round(c.feed_in, 4),
    }


def string_worth_dict(w: StringWorth) -> dict[str, Any]:
    """What a string added over the hours with string readings, as they were:
    not scaled to a year, so a short history says so instead."""
    c = w.comparison

    def run(o: Outcome) -> dict[str, Any]:
        return {
            "import_kwh": round(o.import_kwh, 1),
            "export_kwh": round(o.export_kwh, 1),
            "solar_kwh": round(o.solar_kwh, 1),
            "self_sufficiency_pct": round(o.self_sufficiency * 100, 1),
            "nights": o.nights,
            "nights_off_grid": o.nights_off_grid,
        }

    return {
        "string": w.string,
        "days": round(c.now.days, 1),
        "made_kwh": round(w.made_kwh, 1),
        "with": run(c.now),
        "without": run(c.changed),
        "value": round(w.value, 2),
        "price": round(c.price, 4),
        "feed_in": round(c.feed_in, 4),
        # The replay with the string beside the meters, over the hours they cover.
        "check": {
            "hours": c.now.hours,
            "metered_hours": w.metered_hours,
            "metered_import_kwh": round(w.metered_import_kwh, 1),
            "metered_export_kwh": round(w.metered_export_kwh, 1),
        },
    }
