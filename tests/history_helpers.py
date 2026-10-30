"""Write hourly statistics into the test recorder, as if days had been lived."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from homeassistant.components.recorder.models import StatisticData, StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.solar_energy_insights import const
from tests.common import ENTITIES, local

# kWh in the clock hour starting at the given hour.
type PerHour = Callable[[int], float]


def sunny_solar(hour: int) -> float:
    return 3.0 if 8 <= hour < 17 else 0.0


def house_with_dinner(hour: int) -> float:
    return 1.6 if hour == 19 else 0.4


def night_import(hour: int) -> float:
    return 0.5 if hour >= 22 or hour < 6 else 0.0


def midday_export(hour: int) -> float:
    return 1.0 if 11 <= hour < 15 else 0.0


DEFAULT_ENERGY: dict[str, PerHour] = {
    const.CONF_SOLAR_ENERGY: sunny_solar,
    const.CONF_HOUSE_ENERGY: house_with_dinner,
    const.CONF_GRID_IMPORT_ENERGY: night_import,
    const.CONF_GRID_EXPORT_ENERGY: midday_export,
    const.CONF_BATTERY_CHARGE_ENERGY: lambda h: 1.0 if 9 <= h < 13 else 0.0,
    const.CONF_BATTERY_DISCHARGE_ENERGY: lambda h: 0.4 if h >= 18 or h < 6 else 0.0,
}


def soc_full_at_one(hour: int) -> float:
    """Up to full at 13:00, then down through the evening to 12 % by 23:00."""
    if hour < 9:
        return 20.0
    if hour <= 13:
        return 20.0 + (hour - 9) * 20
    return max(100.0 - (hour - 13) * 9, 12.0)


async def import_days(
    hass: HomeAssistant,
    days: list[date],
    energy: dict[str, PerHour] | None = None,
    soc: PerHour = soc_full_at_one,
) -> None:
    """Record whole days of hourly statistics for every configured entity."""
    energy = {**DEFAULT_ENERGY, **(energy or {})}
    hours = [local(day, 0) + timedelta(hours=h) for day in days for h in range(24)]
    for key, per_hour in energy.items():
        # The meter reads on from 1000 kWh; the recorder's sum starts at zero.
        total = 0.0
        rows: list[StatisticData] = []
        for start in hours:
            total += per_hour(start.hour)
            rows.append(StatisticData(start=start, state=1000.0 + total, sum=total))
        async_import_statistics(
            hass,
            {
                "mean_type": StatisticMeanType.NONE,
                "has_sum": True,
                "name": None,
                "source": "recorder",
                "statistic_id": ENTITIES[key],
                "unit_class": "energy",
                "unit_of_measurement": "kWh",
            },
            rows,
        )
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
        [
            {
                "start": start,
                "mean": soc(start.hour),
                "min": soc(start.hour) - 1 if start.hour > 13 else soc(start.hour),
                "max": soc(start.hour),
            }
            for start in hours
        ],
    )
    await async_wait_recording_done(hass)
