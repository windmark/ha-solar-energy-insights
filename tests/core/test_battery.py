"""The battery simulation keeps to physics and to its limits."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.solar_energy_insights.core.battery import (
    Battery,
    held_floor,
    simulate,
)
from tests.core.helpers import at, constant, sunny

DAY = date(2026, 6, 10)


def battery(soc: float = 50.0, **kwargs) -> Battery:
    return Battery(capacity_kwh=10.0, soc_pct=soc, floor_pct=10.0, **kwargs)


@pytest.mark.parametrize("soc", (5.0, 30.0, 80.0, 100.0))
@pytest.mark.parametrize("load_kw", (0.2, 1.5, 6.0))
def test_stays_between_its_floor_and_full(soc, load_kw) -> None:
    sim = simulate(
        battery(soc), at(DAY, 0), at(DAY, 23), sunny(DAY).kw_at, constant(load_kw)
    )

    levels = [pct for _, pct in sim.points[1:]]
    assert max(levels) <= 100 + 1e-9
    assert min(levels) >= min(soc, 10.0) - 1e-9


@pytest.mark.parametrize("load_kw", (0.3, 2.0, 7.0))
def test_never_creates_energy(load_kw) -> None:
    start = battery(40.0)
    sim = simulate(
        start,
        at(DAY, 0),
        at(DAY + timedelta(1), 0),
        sunny(DAY).kw_at,
        constant(load_kw),
    )

    sources = sim.pv_kwh + sim.grid_kwh + start.stored_kwh
    sinks = sim.load_kwh + sim.export_kwh + sim.export_before_full_kwh + sim.end_kwh
    assert sources >= sinks - 1e-6


def test_more_sun_fills_it_sooner() -> None:
    dull = simulate(
        battery(20), at(DAY, 6), at(DAY, 21), sunny(DAY, peak_kw=4).kw_at, constant(0.5)
    )
    bright = simulate(
        battery(20), at(DAY, 6), at(DAY, 21), sunny(DAY, peak_kw=9).kw_at, constant(0.5)
    )

    assert bright.full_at is not None
    assert dull.full_at is None or bright.full_at < dull.full_at


def test_more_load_reaches_the_reserve_sooner() -> None:
    night = sunny(DAY)
    light = simulate(
        battery(60), at(DAY, 21), at(DAY + timedelta(1), 6), night.kw_at, constant(0.4)
    )
    heavy = simulate(
        battery(60), at(DAY, 21), at(DAY + timedelta(1), 6), night.kw_at, constant(1.5)
    )

    assert light.lasts
    assert heavy.reserve_at is not None
    assert heavy.grid_kwh > 0


def test_charging_power_limit_spills_surplus_before_full() -> None:
    limited = battery(10, max_power_kw=2.0)

    sim = simulate(limited, at(DAY, 10), at(DAY, 14), constant(8.0), constant(0.5))

    assert sim.export_before_full_kwh > 0


def test_a_full_battery_exports_the_surplus() -> None:
    sim = simulate(battery(100), at(DAY, 11), at(DAY, 13), constant(5.0), constant(1.0))

    assert sim.full_at == at(DAY, 11)
    assert sim.export_kwh == pytest.approx(8.0)


def test_spare_tonight_ignores_this_mornings_low() -> None:
    morning_low = battery(15)
    sim = simulate(
        morning_low,
        at(DAY, 7),
        at(DAY + timedelta(1), 5),
        sunny(DAY).kw_at,
        constant(0.4),
        mark_at=at(DAY, 21),
    )

    assert sim.spare_kwh > morning_low.usable_kwh


def test_a_low_level_that_stays_while_the_grid_supplies_the_house_is_held() -> None:
    assert held_floor(1.0, 0.0, timedelta(minutes=30), import_kw=0.4) == 1.0


@pytest.mark.parametrize(
    ("soc", "floor", "unchanged_min", "import_kw"),
    (
        # Covering the house: the grid supplies next to nothing.
        (1.0, 0.0, 60, 0.02),
        # Just got there: a slow discharge takes a while to show a percent.
        (1.0, 0.0, 5, 0.4),
        # At its floor already.
        (5.0, 5.0, 60, 0.4),
        # Above where an inverter stops: a battery kept for later.
        (3.0, 0.0, 60, 0.4),
    ),
)
def test_a_battery_that_may_still_give_is_not_held(
    soc, floor, unchanged_min, import_kw
) -> None:
    assert held_floor(soc, floor, timedelta(minutes=unchanged_min), import_kw) is None


def test_held_it_has_nothing_left_to_give() -> None:
    reported = Battery(capacity_kwh=10.0, soc_pct=1.0, floor_pct=0.0)
    floor = held_floor(reported.soc_pct, reported.floor_pct, timedelta(hours=1), 0.4)
    assert floor is not None
    held = Battery(capacity_kwh=10.0, soc_pct=1.0, floor_pct=floor)

    sim = simulate(held, at(DAY, 20), at(DAY, 23), constant(0.0), constant(0.4))

    assert reported.usable_kwh > 0
    assert held.usable_kwh == 0
    assert sim.reserve_at == at(DAY, 20)
    assert sim.grid_kwh == pytest.approx(1.2)
