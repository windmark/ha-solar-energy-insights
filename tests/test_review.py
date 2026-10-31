"""Found in a full review, at the Home Assistant level: each failed before its fix."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.solar_energy_insights import const
from custom_components.solar_energy_insights.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.common import DAY, local, recent, record_house, set_states, setup_entry, state
from tests.test_answers import evening_load

PREFIX = "solar_energy_insights_"


async def test_a_sleeping_battery_reading_zero_is_at_its_reserve(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Some inverters report 0 % while the battery sleeps at its reserve. The
    plan must not believe the battery is below a floor it cannot go under."""
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 3))
    set_states(home, soc=0, reserve=10)
    entry = await setup_entry(home)

    download = json.loads(
        json.dumps(await async_get_config_entry_diagnostics(home, entry), default=str)
    )

    assert download["replay"]["battery"]["soc_pct"] == 10


async def test_every_saved_morning_is_learned_from_even_with_gaps(
    home: HomeAssistant,
) -> None:
    """Saved mornings with gaps between them: production must be read back to
    the oldest one, not only as many days as there are mornings."""
    await record_house(home, recent(14), evening_load)
    set_states(home)
    solar = "sensor.solar_energy"
    entry = await setup_entry(home, {const.CONF_SOLAR_ENERGY: solar})
    memory = entry.runtime_data.memory.memory
    mornings = [DAY - timedelta(days=2 * n) for n in range(1, 21)]
    for day in mornings:
        memory.forecasts[day.isoformat()] = [
            [local(day, h, m).isoformat(), 2.0, 1.0, 2.2]
            for h in range(8, 17)
            for m in (0, 30)
        ]
    total, rows = 0.0, []
    for n in range(41, 0, -1):
        day = DAY - timedelta(days=n)
        for h in range(24):
            total += 1.6 if 8 <= h < 17 else 0.0
            rows.append(StatisticData(start=local(day, h), state=total, sum=total))
    async_import_statistics(
        home,
        {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": None,
            "source": "recorder",
            "statistic_id": solar,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        rows,
    )
    await async_wait_recording_done(home)

    await entry.runtime_data.async_refresh_history()

    assert entry.runtime_data.correction.days == 20


async def test_a_recorder_hiccup_keeps_the_answers(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)
    await setup_entry(home)

    with patch(
        "custom_components.solar_energy_insights.coordinator.statistics_during_period",
        side_effect=RuntimeError("database is locked"),
    ):
        freezer.tick(timedelta(hours=1))
        async_fire_time_changed(home)
        await home.async_block_till_done(wait_background_tasks=True)

        assert state(home, f"sensor.{PREFIX}nights_covered") not in ("unavailable", "unknown")


async def test_answers_keep_long_term_statistics(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)

    await setup_entry(home)

    for key in (
        "nights_covered",
        "spare_at_sunrise",
        "charge_needed_for_the_night",
        "battery_at_sunset",
        "night_prediction_error",
    ):
        sensor = home.states.get(f"sensor.{PREFIX}{key}")
        assert sensor is not None, key
        assert sensor.attributes.get("state_class") == "measurement", key
