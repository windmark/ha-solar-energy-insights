"""Forecast checkpoints: frozen at 06:00, 12:00, 18:00 and 21:00, judged later."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    StatisticMeanType,
    async_import_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.solar_energy_insights import const
from tests.common import DAY, local, set_states, setup_entry

SOLAR = "sensor.solar_total"
SENSOR = "sensor.solar_energy_insights_forecast_checkpoints"


def view_of(home: HomeAssistant) -> dict:
    state = home.states.get(SENSOR)
    assert state is not None
    return dict(state.attributes)


def solcast_polled(home: HomeAssistant, when: str) -> None:
    entry = er.async_get(home).async_get_or_create(
        "sensor",
        "solcast_solar",
        "solcast_api_last_updated",
        suggested_object_id="solcast_api_last_polled",
    )
    home.states.async_set(entry.entity_id, when)


async def record_solar(home: HomeAssistant, day, kwh_per_hour: float) -> None:
    """The solar counter, kwh_per_hour from 08:00 to 16:00."""
    total, rows = 0.0, []
    for h in range(24):
        total += kwh_per_hour if 8 <= h < 16 else 0.0
        rows.append(StatisticData(start=local(day, h), state=total, sum=total))
    async_import_statistics(
        home,
        StatisticMetaData(
            has_mean=False,
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
            name=None,
            source="recorder",
            statistic_id=SOLAR,
            unit_class="energy",
            unit_of_measurement="kWh",
        ),
        rows,
    )
    await async_wait_recording_done(home)


async def test_the_forecast_is_frozen_at_six_with_solcasts_refresh_time(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local(DAY, 5, 59) + timedelta(seconds=59))
    set_states(home, peak_kw=6)
    solcast_polled(home, local(DAY, 5, 40).isoformat())
    entry = await setup_entry(home)

    freezer.move_to(local(DAY, 6))
    async_fire_time_changed(home, local(DAY, 6))
    await home.async_block_till_done()

    saved = entry.runtime_data.memory.memory.checkpoints[f"{DAY.isoformat()}|06:00"]
    assert saved["polled_at"] == local(DAY, 5, 40).isoformat()
    view = view_of(home)
    assert view["today"]["06:00"]["forecast_kwh"] > 0


async def test_the_evening_checkpoints_freeze_tomorrow(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local(DAY, 20, 59) + timedelta(seconds=59))
    set_states(home, peak_kw=6)
    entry = await setup_entry(home)

    freezer.move_to(local(DAY, 21))
    async_fire_time_changed(home, local(DAY, 21))
    await home.async_block_till_done()

    tomorrow = (DAY + timedelta(days=1)).isoformat()
    assert f"{tomorrow}|21:00" in entry.runtime_data.memory.memory.checkpoints


async def test_a_closed_day_is_judged_against_the_solar_counter(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    yesterday = DAY - timedelta(days=1)
    await record_solar(home, yesterday, 1.5)  # 12 kWh, 6 of them after noon
    freezer.move_to(local(DAY, 9))
    set_states(home, peak_kw=6)
    entry = await setup_entry(home, {const.CONF_SOLAR_ENERGY: SOLAR})
    c = entry.runtime_data
    slots = [
        [
            local(yesterday, 8, 0).isoformat()
            if k == 0
            else (local(yesterday, 8) + timedelta(minutes=30 * k)).isoformat(),
            2.0,
            1.0,
            3.0,
        ]
        for k in range(16)
    ]
    for name in ("06:00", "12:00"):
        c.memory.memory.checkpoints[f"{yesterday.isoformat()}|{name}"] = {
            "name": name,
            "day": yesterday.isoformat(),
            "taken_at": local(yesterday, 6).isoformat(),
            "polled_at": None,
            "slots": slots,
        }

    await c.checkpoints.async_update()

    judged = c.memory.memory.judged
    morning = {r["name"]: r for r in judged[f"{yesterday.isoformat()}|06:00"]["rows"]}
    assert morning["06:00"]["forecast_kwh"] == pytest.approx(16.0)
    assert morning["06:00"]["actual_kwh"] == pytest.approx(12.0)
    assert morning["06:00@noon"]["actual_kwh"] == pytest.approx(6.0)
    noon = judged[f"{yesterday.isoformat()}|12:00"]["rows"][0]
    assert noon["forecast_kwh"] == pytest.approx(8.0)
    assert noon["actual_kwh"] == pytest.approx(6.0)
    assert c.checkpoints.scores["06:00"]["days"] == 1


async def test_without_a_solar_counter_the_sensor_says_what_it_needs(
    home: HomeAssistant,
) -> None:
    set_states(home)

    await setup_entry(home)

    view = view_of(home)
    assert view["needs_solar_counter"] is True
