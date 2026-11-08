"""The scenario action: the last year replayed with a bigger battery or a heat
pump, from the recorder's hourly statistics."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import UnitOfEnergy, UnitOfTemperature
from homeassistant.util import dt as dt_util

from .const import CONF_HOUSE_ENERGY, CONF_OUTDOOR_TEMPERATURE, CONF_SOLAR_ENERGY
from .core.nights import plausible
from .core.scenario import Comparison, Hour, Outcome

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import InsightsCoordinator

SCENARIO_DAYS = 365
# The year's hours are read once in a while, not on every question.
REREAD_AFTER = timedelta(hours=6)
UNITS = {
    "energy": UnitOfEnergy.KILO_WATT_HOUR,
    "temperature": UnitOfTemperature.CELSIUS,
}


async def recorded_hours(
    hass: HomeAssistant,
    house: str,
    solar: str,
    temperature: str | None,
    now: datetime,
) -> list[Hour]:
    """The hours of the last year with both the house's use and the solar."""
    end = now.replace(minute=0, second=0, microsecond=0)
    ids = {house, solar} | ({temperature} if temperature else set())
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

    used, made = changes(house), changes(solar)
    temps = {
        dt_util.utc_from_timestamp(row["start"]): mean
        for row in rows.get(temperature or "", [])
        if (mean := row.get("mean")) is not None
    }
    return [Hour(t, made[t], used[t], temps.get(t)) for t in sorted(used) if t in made]


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
            self._hours = await recorded_hours(
                self.coordinator.hass,
                self.coordinator.config_entry.data[CONF_HOUSE_ENERGY],
                solar,
                self.temperature,
                now,
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
