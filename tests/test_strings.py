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
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.solar_energy_insights import sensor as sensor_module
from custom_components.solar_energy_insights.dashboard import (
    string_panel_w_key,
    string_panels_key,
    string_roof_key,
)
from custom_components.solar_energy_insights.strings import repair_energy_sums
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
    watts(home, strings[1], 1260)

    await setup_entry(home, {string_panels_key(1): 12, string_panel_w_key(1): 420})
    await home.async_block_till_done()

    assert float(state_of(home, f"{PREFIX}pv1_energy_today")) == pytest.approx(
        8.0
    )  # 4 hours of 2 kW
    assert float(state_of(home, f"{PREFIX}pv1_power_per_kwp")) == pytest.approx(0.25)
    assert float(state_of(home, f"{PREFIX}pv1_yield_today")) == pytest.approx(
        8.0 / 5.04, rel=1e-3
    )

    watts(home, strings[1], 2520)
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


async def hourly(home: HomeAssistant, entity_id: str, start: Any) -> list[Any]:
    rows: dict[str, Any] = await get_instance(home).async_add_executor_job(
        statistics_during_period,
        home,
        dt_util.as_utc(start),
        None,
        {entity_id},
        "hour",
        None,
        {"sum", "state"},
    )
    return rows.get(entity_id) or []


async def test_the_backfill_ends_where_the_recorder_starts_its_total(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The recorder starts the sensor's running total at zero; the backfilled
    history ends there too, so no month or year across the install comes out
    below zero."""
    strings = powerocean_strings(home, (3,))
    past = [DAY - timedelta(days=k) for k in (3, 2, 1)]
    await record_power(home, strings[3], past, 1.5)
    freezer.move_to(local(DAY, 9))
    set_states(home)

    await setup_entry(home)
    await home.async_block_till_done()
    await async_wait_recording_done(home)

    rows = await hourly(home, f"{PREFIX}pv3_energy_today", local(past[0], 0))
    assert rows[-1]["sum"] == pytest.approx(0.0)
    assert rows[0]["sum"] == pytest.approx(-18.0 + 1.5)
    assert [r["state"] for r in rows[-4:]] == pytest.approx([1.5, 3.0, 4.5, 6.0])


async def test_a_running_total_that_dropped_is_lifted_back_into_line(
    home: HomeAssistant,
) -> None:
    """An install from before: the backfill's total ran up to 12 kWh, then the
    recorder started its own at zero. Every later hour moves up with the repair;
    the next day's first hour adds that day's energy, not the difference."""
    energy = f"{PREFIX}pv1_energy_today"
    day1, day2 = DAY - timedelta(days=2), DAY - timedelta(days=1)
    points = [
        (local(day1, 10), 3.0, 3.0),
        (local(day1, 11), 6.0, 6.0),
        (local(day1, 12), 9.0, 9.0),
        (local(day1, 13), 12.0, 12.0),
        # the recorder's own total, from zero
        (local(day2, 10), 2.0, 0.0),
        (local(day2, 11), 5.0, 3.0),
        (local(day2, 12), 7.0, 5.0),
    ]
    async_import_statistics(
        home,
        StatisticMetaData(
            has_mean=False,
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
            name=None,
            source="recorder",
            statistic_id=energy,
            unit_class="energy",
            unit_of_measurement="kWh",
        ),
        [StatisticData(start=t, state=st, sum=sm) for t, st, sm in points],
    )
    await async_wait_recording_done(home)

    assert await repair_energy_sums(home, energy) == 1
    await async_wait_recording_done(home)
    rows = await hourly(home, energy, local(day1, 0))
    assert [r["sum"] for r in rows] == pytest.approx(
        [3.0, 6.0, 9.0, 12.0, 14.0, 17.0, 19.0]
    )
    assert await repair_energy_sums(home, energy) == 0  # once only


async def test_todays_energy_never_dips_until_the_day_turns(
    home: HomeAssistant, freezer: FrozenDateTimeFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Just after the hour, the hour that ended isn't in the statistics yet: the
    sensor holds its value rather than dip, which would read as a meter reset."""
    powerocean_strings(home, (1,))
    readings = [8.0, 6.5, 9.0, 1.0]

    async def fake(*_: Any) -> float:
        return readings.pop(0) if len(readings) > 1 else readings[0]

    monkeypatch.setattr(sensor_module, "energy_today", fake)
    freezer.move_to(local(DAY, 15))
    set_states(home)
    await setup_entry(home)
    await home.async_block_till_done()
    energy = f"{PREFIX}pv1_energy_today"
    seen = [float(state_of(home, energy))]
    for when in (
        local(DAY, 15, 6),
        local(DAY, 15, 12),
        local(DAY + timedelta(days=1), 0, 6),
    ):
        freezer.move_to(when)
        async_fire_time_changed(home)
        await home.async_block_till_done()
        seen.append(float(state_of(home, energy)))
    assert seen == [8.0, 8.0, 9.0, 1.0]


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


async def test_power_per_kwp_is_written_when_it_matters(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Each write goes into the recorder: a meaningful change at once, small
    drift at most every 30 seconds."""
    strings = powerocean_strings(home, (1,))
    set_states(home)
    watts(home, strings[1], 2000)
    await setup_entry(home, {string_panels_key(1): 10, string_panel_w_key(1): 500})
    await home.async_block_till_done()
    entity = f"{PREFIX}pv1_power_per_kwp"
    writes: list[str] = []
    home.bus.async_listen(
        "state_changed",
        lambda e: (
            writes.append(e.data["new_state"].state)
            if e.data["entity_id"] == entity
            else None
        ),
    )

    async def power(w: float, after_s: float = 5) -> None:
        freezer.tick(timedelta(seconds=after_s))
        watts(home, strings[1], w)
        await home.async_block_till_done()

    for w in (2010, 2020, 2030, 2040):  # 0.002 kW/kWp steps: drift
        await power(w)
    assert writes == []
    await power(2045, after_s=15)  # 30 s since the last write: drift is written
    assert writes == ["0.409"]
    await power(2600)  # a cloud clears: written at once
    assert writes == ["0.409", "0.52"]
    home.states.async_set(strings[1], "unavailable")
    await home.async_block_till_done()
    assert writes[-1] == "unavailable"
