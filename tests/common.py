"""A small home in Augsburg: a battery, panels, Solcast and the counters."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_energy_insights import const

TZ = "Europe/Berlin"
DAY = date(2026, 6, 10)

ENTITIES = {
    const.CONF_BATTERY_SOC: "sensor.battery_soc",
    const.CONF_BATTERY_POWER: "sensor.battery_power",
    const.CONF_BATTERY_CAPACITY: "sensor.battery_capacity",
    const.CONF_BATTERY_RESERVE: "sensor.battery_reserve",
    const.CONF_SOLAR_POWER: "sensor.solar_power",
    const.CONF_HOUSE_POWER: "sensor.house_power",
    const.CONF_GRID_POWER: "sensor.grid_power",
    const.CONF_SOLAR_ENERGY: "sensor.solar_energy",
    const.CONF_HOUSE_ENERGY: "sensor.house_energy",
    const.CONF_GRID_IMPORT_ENERGY: "sensor.grid_import",
    const.CONF_GRID_EXPORT_ENERGY: "sensor.grid_export",
    const.CONF_BATTERY_CHARGE_ENERGY: "sensor.battery_charged",
    const.CONF_BATTERY_DISCHARGE_ENERGY: "sensor.battery_discharged",
    const.CONF_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
    const.CONF_SOLCAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
}


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=dt_util.get_default_time_zone(),
    )


def solcast_periods(day: date, peak_kw: float) -> list[dict[str, Any]]:
    periods = []
    for k in range(32):
        kw = peak_kw * math.sin(math.pi * (k + 0.5) / 32)
        periods.append(
            {
                "period_start": local(day, 5) + timedelta(minutes=30 * k),
                "pv_estimate": round(kw, 3),
                "pv_estimate10": round(kw * 0.6, 3),
                "pv_estimate90": round(kw * 1.1, 3),
            }
        )
    return periods


async def configure_home(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone(TZ)
    hass.config.latitude, hass.config.longitude = 48.37, 10.9


def set_states(
    hass: HomeAssistant,
    *,
    soc: float = 50.0,
    capacity_wh: float | str = 15180,
    reserve: float = 10.0,
    solar_w: float = 3000,
    house_w: float = 600,
    grid_w: float = 0,
    peak_kw: float = 8.0,
) -> None:
    def power(entity_id: str, watts: float) -> None:
        hass.states.async_set(
            entity_id, str(watts), {"unit_of_measurement": "W", "device_class": "power"}
        )

    def energy(entity_id: str, kwh: float) -> None:
        hass.states.async_set(
            entity_id,
            str(kwh),
            {
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing",
            },
        )

    hass.states.async_set(
        ENTITIES[const.CONF_BATTERY_SOC],
        str(soc),
        {"unit_of_measurement": "%", "device_class": "battery"},
    )
    hass.states.async_set(
        ENTITIES[const.CONF_BATTERY_CAPACITY],
        str(capacity_wh),
        {"unit_of_measurement": "Wh", "device_class": "energy_storage"},
    )
    hass.states.async_set(
        ENTITIES[const.CONF_BATTERY_RESERVE], str(reserve), {"unit_of_measurement": "%"}
    )
    power(ENTITIES[const.CONF_BATTERY_POWER], 0)
    power(ENTITIES[const.CONF_SOLAR_POWER], solar_w)
    power(ENTITIES[const.CONF_HOUSE_POWER], house_w)
    power(ENTITIES[const.CONF_GRID_POWER], grid_w)
    for key, value in (
        (const.CONF_SOLAR_ENERGY, 1000.0),
        (const.CONF_HOUSE_ENERGY, 800.0),
        (const.CONF_GRID_IMPORT_ENERGY, 300.0),
        (const.CONF_GRID_EXPORT_ENERGY, 400.0),
        (const.CONF_BATTERY_CHARGE_ENERGY, 200.0),
        (const.CONF_BATTERY_DISCHARGE_ENERGY, 180.0),
    ):
        energy(ENTITIES[key], value)
    today = dt_util.as_local(dt_util.utcnow()).date()
    for key, day in (
        (const.CONF_SOLCAST_TODAY, today),
        (const.CONF_SOLCAST_TOMORROW, today + timedelta(1)),
    ):
        periods = solcast_periods(day, peak_kw)
        total = sum(p["pv_estimate"] for p in periods) / 2
        hass.states.async_set(
            ENTITIES[key],
            str(round(total, 2)),
            {"unit_of_measurement": "kWh", "detailedForecast": periods},
        )


async def setup_entry(
    hass: HomeAssistant,
    options: dict[str, Any] | None = None,
    subentries: list[Any] | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        title="Solar Energy Insights",
        data=dict(ENTITIES),
        options=options or {},
        subentries_data=subentries or [],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    # Includes the catch-up from history, which reads the recorder.
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def state(hass: HomeAssistant, entity_id: str) -> str:
    """The state of an entity that must exist."""
    current = hass.states.get(entity_id)
    assert current is not None, entity_id
    return current.state
