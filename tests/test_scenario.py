"""The scenario action: the recorded weeks again, with more battery or a heat pump."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pytest
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.solar_energy_insights import const
from tests.common import local, recent, record_energy, record_house, set_states
from tests.common import setup_entry as setup

SOLAR = "sensor.solar_energy"
OUTDOOR = "sensor.outdoor_temperature"
DAYS = recent(30)


def evening(hour: int) -> float:
    return 0.9 if 17 <= hour < 22 else 0.35


def sun(_day: date, hour: int) -> float:
    return 6.0 * math.sin(math.pi * (hour - 5) / 15) if 5 <= hour < 20 else 0.0


async def record_temperature(home: HomeAssistant, celsius: float) -> None:
    rows = [
        StatisticData(start=local(day, hour), mean=celsius, min=celsius, max=celsius)
        for day in DAYS
        for hour in range(24)
    ]
    async_import_statistics(
        home,
        {
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": OUTDOOR,
            "unit_class": "temperature",
            "unit_of_measurement": "°C",
        },
        rows,
    )
    await async_wait_recording_done(home)


async def setup_entry(home: HomeAssistant, **options: Any) -> None:
    await record_house(home, DAYS, evening)
    await record_energy(home, SOLAR, DAYS, sun)
    set_states(home)
    await setup(home, {const.CONF_SOLAR_ENERGY: SOLAR, **options})


async def scenario(home: HomeAssistant, **data: Any) -> Any:
    return await home.services.async_call(
        const.DOMAIN, "scenario", data, blocking=True, return_response=True
    )


async def test_more_battery_buys_less_from_the_grid(home: HomeAssistant) -> None:
    await setup_entry(home)

    result = await scenario(home, extra_battery_kwh=5, battery_price=2500)

    assert result["days"] == pytest.approx(30, abs=1)
    assert result["changed"]["import_kwh"] <= result["now"]["import_kwh"]
    assert (
        result["changed"]["self_sufficiency_pct"]
        >= result["now"]["self_sufficiency_pct"]
    )
    assert result["saving_per_year"] >= 0


async def test_nothing_changed_saves_nothing(home: HomeAssistant) -> None:
    await setup_entry(home)

    result = await scenario(home)

    assert result["now"] == result["changed"]
    assert result["saving_per_year"] == 0
    assert result["payback_years"] is None


async def test_a_heat_pump_in_the_cold_uses_more_than_the_roof_gives(
    home: HomeAssistant,
) -> None:
    await record_temperature(home, 2.0)
    await setup_entry(home, **{const.CONF_OUTDOOR_TEMPERATURE: OUTDOOR})

    result = await scenario(home, heat_loss_w_per_k=180, hot_water_kwh_per_day=6)

    assert result["changed"]["heat_pump_kwh"] > 5_000
    assert result["changed"]["import_kwh"] > result["now"]["import_kwh"]
    assert result["saving_per_year"] < 0
    assert 0 <= result["heat_pump_solar_pct"] <= 100


async def test_a_heat_pump_needs_the_outdoor_temperature(home: HomeAssistant) -> None:
    await setup_entry(home)

    with pytest.raises(ServiceValidationError) as err:
        await scenario(home, heat_loss_w_per_k=180)

    assert err.value.translation_key == "needs_temperature"


async def test_the_scenario_needs_the_solar_counter(home: HomeAssistant) -> None:
    await record_house(home, DAYS, evening)
    set_states(home)
    await setup(home)

    with pytest.raises(ServiceValidationError) as err:
        await scenario(home, extra_battery_kwh=5)

    assert err.value.translation_key == "needs_solar_counter"


async def test_too_little_history_is_refused(home: HomeAssistant) -> None:
    await record_house(home, recent(3), evening)
    await record_energy(home, SOLAR, recent(3), sun)
    set_states(home)
    await setup(home, {const.CONF_SOLAR_ENERGY: SOLAR})

    with pytest.raises(ServiceValidationError) as err:
        await scenario(home, extra_battery_kwh=5)

    assert err.value.translation_key == "too_little_history"
