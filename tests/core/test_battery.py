"""The battery simulation keeps to physics and to its limits."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.solar_energy_insights.core.battery import Battery, simulate
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


def test_a_lower_discharge_limit_hands_over_to_the_grid_sooner() -> None:
    day = date(2026, 12, 10)
    full = Battery(capacity_kwh=10.0, soc_pct=90.0, floor_pct=5.0, max_power_kw=5.0)
    throttled = Battery(
        capacity_kwh=10.0,
        soc_pct=90.0,
        floor_pct=5.0,
        max_power_kw=5.0,
        max_discharge_kw=1.5,
    )

    a = simulate(full, at(day, 18), at(day, 20), lambda _t: 0.0, constant(3.0))
    b = simulate(throttled, at(day, 18), at(day, 20), lambda _t: 0.0, constant(3.0))

    assert a.grid_kwh == 0
    assert b.grid_kwh > 2.5
