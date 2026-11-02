"""Each PowerOcean string as sensors, and this morning's forecast."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    StatisticMeanType,
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.solar_energy_insights.dashboard import (
    string_kwp_key,
    string_roof_key,
)
from tests.common import DAY, local, set_states, setup_entry
from tests.test_config_flow import suggested
from tests.test_dashboard import config_of, powerocean_strings
from tests.test_devices import solcast_site

PREFIX = "sensor.solar_energy_insights_"


def state_of(home: HomeAssistant, entity_id: str) -> str:
    state = home.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def record_power(
    home: HomeAssistant, entity_id: str, days: list[date], kw: float
) -> None:
    """A string's hourly mean power, kW, from 10:00 to 14:00 each day."""
    rows = [
        StatisticData(start=local(d, h), mean=kw, min=kw, max=kw)
        for d in days
        for h in range(10, 14)
    ]
    async_import_statistics(
        home,
        StatisticMetaData(
            has_mean=True,
            has_sum=False,
            mean_type=StatisticMeanType.ARITHMETIC,
            name=None,
            source="recorder",
            statistic_id=entity_id,
            unit_class="power",
            unit_of_measurement="kW",
        ),
        rows,
    )
    await async_wait_recording_done(home)


def watts(home: HomeAssistant, entity_id: str, w: float) -> None:
    home.states.async_set(
        entity_id, str(w), {"unit_of_measurement": "W", "device_class": "power"}
    )


async def test_each_string_reports_energy_power_and_yield_per_kwp(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    strings = powerocean_strings(home, (1,))
    await record_power(home, strings[1], [DAY], 2.0)
    freezer.move_to(local(DAY, 15))
    set_states(home)
    watts(home, strings[1], 1265)

    await setup_entry(home, {string_kwp_key(1): 5.06})
    await home.async_block_till_done()

    assert float(state_of(home, f"{PREFIX}pv1_energy_today")) == pytest.approx(
        8.0
    )  # 4 hours of 2 kW
    assert float(state_of(home, f"{PREFIX}pv1_power_per_kwp")) == pytest.approx(0.25)
    assert float(state_of(home, f"{PREFIX}pv1_yield_today")) == pytest.approx(
        8.0 / 5.06, rel=1e-3
    )

    watts(home, strings[1], 2530)
    await home.async_block_till_done()
    assert float(state_of(home, f"{PREFIX}pv1_power_per_kwp")) == pytest.approx(0.5)


async def test_without_a_size_per_kwp_is_not_guessed(home: HomeAssistant) -> None:
    strings = powerocean_strings(home, (2,))
    set_states(home)
    watts(home, strings[2], 1000)

    await setup_entry(home)

    assert state_of(home, f"{PREFIX}pv2_power_per_kwp") == "unavailable"
    assert state_of(home, f"{PREFIX}pv2_yield_today") == "unavailable"
    assert state_of(home, f"{PREFIX}pv2_energy_today") != "unavailable"


async def test_the_energy_sensor_starts_with_the_strings_whole_history(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    strings = powerocean_strings(home, (3,))
    past = [DAY - timedelta(days=k) for k in (3, 2, 1)]
    await record_power(home, strings[3], past, 1.5)
    freezer.move_to(local(DAY, 9))
    set_states(home)

    await setup_entry(home)
    await home.async_block_till_done()
    await async_wait_recording_done(home)

    energy = f"{PREFIX}pv3_energy_today"
    rows: dict[str, Any] = await get_instance(home).async_add_executor_job(
        statistics_during_period,
        home,
        dt_util.as_utc(local(past[0], 0)),
        dt_util.as_utc(local(DAY, 0)),
        {energy},
        "day",
        None,
        {"change"},
    )
    assert [round(r["change"], 2) for r in rows[energy]] == [6.0, 6.0, 6.0]


async def test_this_mornings_forecast_is_kept_day_by_day(home: HomeAssistant) -> None:
    set_states(home)
    entry = await setup_entry(home)
    memory = entry.runtime_data.memory.memory
    memory.forecasts[DAY.isoformat()] = [
        [local(DAY, 11).isoformat(), 4.0, 3.0, 5.0],
        [local(DAY, 11, 30).isoformat(), 2.0, 1.0, 3.0],
    ]
    entry.runtime_data.async_update_listeners()
    await home.async_block_till_done()

    morning = home.states.get(f"{PREFIX}morning_forecast")
    assert morning is not None
    found = morning.attributes["today"] or next(
        m for m in morning.attributes["history"] if m["d"] == DAY.isoformat()
    )
    assert found["mo"] == pytest.approx(3.0)
    assert found["moh"][11] == pytest.approx(3.0)


async def test_each_string_is_placed_on_a_solcast_roof(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    powerocean_strings(home, (1, 3))
    set_states(home)
    solcast_site(home, "South roof", 8.74, 172, 31)
    solcast_site(home, "North roof", 6.44, 352, 31)
    entry = await setup_entry(
        home, {string_roof_key(1): "South roof", string_roof_key(3): "North roof"}
    )

    form = await home.config_entries.options.async_init(entry.entry_id)
    cfg = await config_of(home, hass_ws_client)

    assert suggested(form, string_roof_key(1)) == "South roof"
    roofs = {s["n"]: s["roof"] for s in cfg["strings"]}
    assert roofs[1]["facing"] == "south"
    assert roofs[3]["facing"] == "north"
    assert [r["name"] for r in cfg["roofs"]] == ["South roof", "North roof"]
