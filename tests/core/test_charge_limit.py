"""A battery set to stop charging below 100 %: full means reaching the limit."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import pytest

from custom_components.solar_energy_insights.core.battery import Battery, simulate
from custom_components.solar_energy_insights.core.plan import plan
from tests.core.helpers import at, constant, sunny
from tests.core.scenarios import SEASONS, heavy_evenings, history, inputs

DAY = date(2026, 6, 10)
SUMMER, WINTER = SEASONS


def day_run(ceiling: float):
    battery = Battery(
        capacity_kwh=10.0, soc_pct=30.0, floor_pct=5.0, ceiling_pct=ceiling
    )
    return simulate(battery, at(DAY, 6), at(DAY, 21), sunny(DAY).kw_at, constant(0.4))


def test_charging_stops_at_the_limit_and_full_means_reaching_it() -> None:
    limited, unlimited = day_run(80.0), day_run(100.0)

    assert limited.peak_pct == pytest.approx(80.0)
    assert limited.full_at is not None
    assert unlimited.full_at is not None
    assert limited.full_at < unlimited.full_at


def test_what_the_battery_no_longer_takes_is_surplus() -> None:
    limited, unlimited = day_run(80.0), day_run(100.0)

    sent = limited.export_kwh + limited.export_before_full_kwh
    sent_unlimited = unlimited.export_kwh + unlimited.export_before_full_kwh
    # 20 % of 10 kWh not stored is that much more sent out, before charging losses.
    assert sent - sent_unlimited == pytest.approx(2.0 / math.sqrt(0.92), rel=0.02)


def test_a_battery_above_a_lowered_limit_is_not_charged_and_counts_as_full() -> None:
    battery = Battery(capacity_kwh=10.0, soc_pct=92.0, floor_pct=5.0, ceiling_pct=80.0)

    run = simulate(battery, at(DAY, 11), at(DAY, 13), sunny(DAY).kw_at, constant(0.3))

    assert battery.is_full
    assert run.full_at == at(DAY, 11)
    assert run.peak_pct == pytest.approx(92.0)


def test_charge_needed_never_asks_for_more_than_the_limit() -> None:
    use = history(WINTER.day, heavy_evenings)
    now = at(WINTER.day, 20)
    base = inputs(WINTER, now, peak_kw=0, use=use, soc=60)

    full = plan(base)
    capped = plan(replace(base, battery=replace(base.battery, ceiling_pct=70.0)))

    assert full.needed is not None
    assert capped.needed is not None
    for pct in (capped.needed.typical_pct, capped.needed.heavy_pct):
        assert pct is None or pct <= 70.0
    # Starting at the limit leaves more short than starting full.
    assert capped.needed.heavy_short_kwh >= full.needed.heavy_short_kwh
    assert capped.charge_limit_pct == 70.0


def test_free_power_starts_at_the_limit() -> None:
    use = history(SUMMER.day, heavy_evenings)
    now = at(SUMMER.day, 12)
    base = inputs(SUMMER, now, peak_kw=9, use=use, soc=80, export_kw=3.0)

    assert not plan(base).free_power
    assert plan(
        replace(base, battery=replace(base.battery, ceiling_pct=80.0))
    ).free_power


def test_a_limit_never_makes_the_night_better() -> None:
    use = history(SUMMER.day, heavy_evenings)
    now = at(SUMMER.day, 9)
    nights = []
    for ceiling in (100.0, 85.0, 70.0):
        base = inputs(SUMMER, now, peak_kw=9, use=use, soc=30)
        night = plan(
            replace(base, battery=replace(base.battery, ceiling_pct=ceiling))
        ).night
        assert night is not None
        nights.append(night)

    spares = [n.typical.spare_kwh for n in nights]
    assert spares == sorted(spares, reverse=True)
