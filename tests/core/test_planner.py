"""The plan answers the questions the dashboard used to answer in the browser."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from custom_components.solar_energy_insights.core.load import LoadModel
from custom_components.solar_energy_insights.core.planner import (
    ApplianceLoad,
    Phase,
    PlanInputs,
    Tone,
    battery_from,
    plan,
    simulate_with,
)
from tests.core.helpers import TZ, at, sunny

DAY = date(2026, 6, 10)
NEXT = DAY + timedelta(1)


def heavy_load(kw: float) -> LoadModel:
    model = LoadModel()
    model.week = [kw] * 168
    return model


def inputs(hour: int, soc: float, **kwargs) -> PlanInputs:
    now = at(DAY, hour)
    defaults: dict[str, Any] = {
        "now": now,
        "tz": TZ,
        "sunrise": at(NEXT, 5, 15) if hour >= 5 else at(DAY, 5, 15),
        "sunset": at(DAY, 21, 20) if hour < 21 else at(NEXT, 21, 20),
        "battery": battery_from(15.0, soc, 10.0, 5.0, 5.0, 0.92),
        "pv": sunny(DAY, NEXT),
        "load": LoadModel(),
        "forecast_today_kwh": 60.0,
        "forecast_next_days": [60.0, 45.0, 30.0],
    }
    defaults.update(kwargs)
    return PlanInputs(**defaults)


def test_a_sunny_morning_fills_the_battery() -> None:
    result = plan(inputs(9, 30))

    assert result.phase is Phase.DAY
    assert result.full_at is not None
    assert result.full_at < at(DAY, 21)
    assert result.tone is Tone.GOOD
    assert result.lasts_night


def test_a_heavy_evening_on_a_low_battery_needs_the_grid() -> None:
    result = plan(inputs(19, 20, load=heavy_load(1.2), pv=sunny(NEXT)))

    assert not result.lasts_night
    assert result.reserve_at is not None
    assert at(DAY, 19) < result.reserve_at < at(NEXT, 5, 15)
    assert result.night_grid_kwh > 0


def test_a_full_battery_exporting_is_free_power() -> None:
    result = plan(inputs(13, 100, export_kw=3.0))

    assert result.free_power
    assert "Free power" in result.headline


def test_an_empty_battery_at_night_is_on_the_grid() -> None:
    result = plan(inputs(23, 9))

    assert result.grid_mode
    assert result.tone is Tone.BAD
    assert result.sun_covers_house_at is not None
    assert result.sun_covers_house_at > at(NEXT, 5)


def test_answers_the_night_without_a_reported_capacity() -> None:
    no_capacity = battery_from(None, 8, 10, 5, 5, 0.92)

    result = plan(inputs(23, 8, battery=no_capacity, capacity_known=False))

    assert result.grid_mode
    assert any("capacity" in line.text for line in result.lines)


def test_an_appliance_that_breaks_the_night_is_named() -> None:
    dishwasher = ApplianceLoad("Dishwasher", kwh=3.0, hours=2.0)

    evening = inputs(
        20,
        45,
        now=at(DAY, 20, 30),
        sunset=at(NEXT, 21, 20),
        pv=sunny(NEXT),
        load=heavy_load(0.6),
        appliances=[dishwasher],
    )

    result = plan(evening)

    assert result.phase is Phase.EVENING
    line = next(line for line in result.lines if line.key == "appliances")
    assert "Dishwasher" in line.label
    assert line.tone is Tone.WARN


@pytest.mark.parametrize("hour", (2, 9, 13, 19, 22))
def test_every_moment_gets_a_headline_and_lines(hour) -> None:
    result = plan(inputs(hour, 50))

    assert result.headline
    assert result.subline
    assert result.lines


def test_running_an_appliance_leaves_less_for_the_night() -> None:
    base = inputs(19, 60)
    alone = simulate_with(base, base.sunrise)
    with_dryer = simulate_with(base, base.sunrise, [ApplianceLoad("Dryer", 2.0, 2.0)])

    assert with_dryer.low_kwh < alone.low_kwh
