"""A small home in Augsburg: a battery, a house energy counter and Solcast."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.recorder.models import StatisticData, StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.solar_energy_insights import const

TZ = "Europe/Berlin"
DAY = date(2026, 6, 10)

ENTITIES = {
    const.CONF_BATTERY_SOC: "sensor.battery_soc",
    const.CONF_BATTERY_CAPACITY: "sensor.battery_capacity",
    const.CONF_BATTERY_RESERVE: "sensor.battery_reserve",
    const.CONF_HOUSE_ENERGY: "sensor.house_energy",
    const.CONF_GRID_POWER: "sensor.grid_power",
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


async def configure_home(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone(TZ)
    hass.config.latitude, hass.config.longitude = 48.37, 10.9


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


def set_states(
    hass: HomeAssistant,
    *,
    soc: float = 50.0,
    capacity_wh: float | str = 15180,
    reserve: float = 10.0,
    grid_w: float = 0,
    peak_kw: float = 8.0,
) -> None:
    hass.states.async_set(
        ENTITIES[const.CONF_BATTERY_SOC],
        str(soc),
        {"unit_of_measurement": "%", "device_class": "battery"},
    )
    hass.states.async_set(
        ENTITIES[const.CONF_BATTERY_CAPACITY],
        str(capacity_wh),
        {"unit_of_measurement": "Wh"},
    )
    hass.states.async_set(
        ENTITIES[const.CONF_BATTERY_RESERVE], str(reserve), {"unit_of_measurement": "%"}
    )
    hass.states.async_set(
        ENTITIES[const.CONF_GRID_POWER],
        str(grid_w),
        {"unit_of_measurement": "W", "device_class": "power"},
    )
    hass.states.async_set(
        ENTITIES[const.CONF_HOUSE_ENERGY],
        "1000",
        {
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
        },
    )
    today = dt_util.as_local(dt_util.utcnow()).date()
    for key, day in (
        (const.CONF_SOLCAST_TODAY, today),
        (const.CONF_SOLCAST_TOMORROW, today + timedelta(days=1)),
    ):
        periods = solcast_periods(day, peak_kw)
        hass.states.async_set(
            ENTITIES[key],
            str(round(sum(p["pv_estimate"] for p in periods) / 2, 2)),
            {"unit_of_measurement": "kWh", "detailedForecast": periods},
        )


async def record_house(
    hass: HomeAssistant, days: list[date], kwh_at: Callable[[int], float]
) -> None:
    """Record whole days of the house's hourly use, as the recorder would have."""
    total = 0.0
    rows: list[StatisticData] = []
    for day in days:
        for hour in range(24):
            total += kwh_at(hour)
            rows.append(
                StatisticData(start=local(day, hour), state=1000 + total, sum=total)
            )
    async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": None,
            "source": "recorder",
            "statistic_id": ENTITIES[const.CONF_HOUSE_ENERGY],
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        rows,
    )
    await async_wait_recording_done(hass)


def recent(count: int) -> list[date]:
    """The *count* days before DAY, oldest first."""
    return [DAY - timedelta(days=n) for n in range(count, 0, -1)]


async def setup_entry(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        title="Solar Energy Insights",
        data=dict(ENTITIES),
        options=options or {},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def state(hass: HomeAssistant, entity_id: str) -> str:
    current = hass.states.get(entity_id)
    assert current is not None, entity_id
    return current.state


async def record_battery(
    hass: HomeAssistant, days: list[date], soc_at: Callable[[int], float]
) -> None:
    """Record the battery's hourly lowest level, as the recorder would have."""
    rows = [
        StatisticData(
            start=local(day, hour),
            mean=soc_at(hour),
            min=soc_at(hour),
            max=soc_at(hour),
        )
        for day in days
        for hour in range(24)
    ]
    async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": ENTITIES[const.CONF_BATTERY_SOC],
            "unit_class": None,
            "unit_of_measurement": "%",
        },
        rows,
    )
    await async_wait_recording_done(hass)
