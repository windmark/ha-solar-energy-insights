"""A feed-in cap: what the inverter would curtail, ahead and so far today."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from custom_components.solar_energy_insights.core.battery import Battery, simulate
from custom_components.solar_energy_insights.core.curtailment import (
    Reading,
    curtailed,
)
from custom_components.solar_energy_insights.core.plan import plan
from tests.core.helpers import at, constant, sunny
from tests.core.scenarios import SEASONS, flat, history, inputs

DAY = date(2026, 6, 10)
SUMMER, WINTER = SEASONS
# A 15 kWp roof on a clear day; the grid takes 60 % of it.
PEAK_KW = 12.0
CAP_KW = 9.1


def day_run(
    soc: float, cap: float | None, ceiling: float = 100.0, capacity: float = 10.0
):
    battery = Battery(
        capacity_kwh=capacity,
        soc_pct=soc,
        floor_pct=5.0,
        max_power_kw=5.0,
        ceiling_pct=ceiling,
    )
    return simulate(
        battery,
        at(DAY, 6),
        at(DAY, 21),
        sunny(DAY, peak_kw=PEAK_KW).kw_at,
        constant(0.5),
        cap_kw=cap,
    )


def test_without_a_cap_nothing_is_clipped() -> None:
    run = day_run(30.0, None)

    assert run.clipped_kwh == 0.0
    assert run.clip_from is None
    assert run.clip_until is None


def test_a_full_battery_at_noon_is_clipped_above_the_cap() -> None:
    run = day_run(30.0, CAP_KW)

    assert run.full_at is not None
    assert run.clipped_kwh > 0.5
    assert run.clip_from is not None
    assert run.clip_until is not None
    # Clipping starts once charging no longer takes the excess.
    assert run.clip_from >= run.full_at - timedelta(minutes=30)
    assert at(DAY, 10) < run.clip_from < at(DAY, 14) < run.clip_until < at(DAY, 17)


def test_what_is_clipped_is_part_of_what_goes_out() -> None:
    run = day_run(30.0, CAP_KW)

    sent = run.export_kwh + run.export_before_full_kwh
    assert run.clipped_kwh < sent


def test_a_higher_cap_clips_less_and_none_above_the_peak() -> None:
    clipped = [day_run(30.0, cap).clipped_kwh for cap in (6.0, 8.0, 10.0, 12.0)]

    assert clipped == sorted(clipped, reverse=True)
    assert clipped[0] > clipped[1]
    assert clipped[-1] == 0.0


def test_room_in_the_battery_takes_what_the_grid_would_not() -> None:
    # A battery large enough to be still charging at noon.
    empty = day_run(10.0, CAP_KW, capacity=40.0)
    nearly_full = day_run(90.0, CAP_KW, capacity=40.0)

    assert empty.clipped_kwh < nearly_full.clipped_kwh


def test_a_battery_full_by_mid_morning_clips_as_much_as_a_full_one() -> None:
    # Why holding room back for midday matters: in automatic, a 10 kWh battery
    # is full before the clipping starts, however empty it was at sunrise.
    assert day_run(10.0, CAP_KW).clipped_kwh == pytest.approx(
        day_run(90.0, CAP_KW).clipped_kwh
    )


def test_a_lowered_charge_limit_clips_sooner() -> None:
    limited = day_run(30.0, CAP_KW, 60.0, capacity=40.0)
    unlimited = day_run(30.0, CAP_KW, capacity=40.0)

    assert limited.clipped_kwh > unlimited.clipped_kwh
    assert limited.clip_from is not None
    assert unlimited.clip_from is not None
    assert limited.clip_from <= unlimited.clip_from


@pytest.mark.parametrize("season", SEASONS, ids=str)
def test_the_plan_reports_clipping_until_sunset(season) -> None:
    use = history(season.day, flat)
    base = inputs(season, at(season.day, 7), peak_kw=PEAK_KW, use=use, soc=80)

    uncapped = plan(base)
    capped = plan(replace(base, export_cap_kw=CAP_KW))

    assert uncapped.surplus.clipped_kwh == 0.0
    assert capped.surplus.clipped_kwh > 0.0
    assert capped.surplus.clipped_cloudy_kwh <= capped.surplus.clipped_kwh
    # The cap changes what is lost, not what the battery does.
    assert capped.charge == uncapped.charge
    assert capped.surplus.total_kwh == pytest.approx(uncapped.surplus.total_kwh)


def test_after_sunset_it_is_about_tomorrow() -> None:
    use = history(SUMMER.day, flat)
    now = at(SUMMER.day, 22)
    capped = plan(
        inputs(SUMMER, now, peak_kw=PEAK_KW, use=use, soc=80, export_cap_kw=CAP_KW)
    )

    assert capped.surplus.clipped_kwh > 0.0
    assert capped.surplus.clip_from is not None
    assert capped.surplus.clip_from > at(SUMMER.day + timedelta(days=1), 6)


def readings(export_kw: float, solar_kw: float, hours: range) -> list[Reading]:
    return [
        Reading(at(DAY, h, m), 5.0, export_kw, solar_kw)
        for h in hours
        for m in range(0, 60, 5)
    ]


def test_curtailment_counts_only_while_the_export_is_at_the_cap() -> None:
    pv = sunny(DAY, peak_kw=PEAK_KW)

    below = curtailed(readings(5.0, 6.0, range(11, 14)), pv, CAP_KW)
    held = curtailed(readings(CAP_KW, 9.6, range(11, 14)), pv, CAP_KW)

    assert below.kwh == 0.0
    assert below.minutes_at_cap == 0.0
    assert held.minutes_at_cap == 180.0
    # The forecast at midday is well above the 9.6 kW made.
    assert held.kwh > 3.0


def test_a_reading_at_the_forecast_is_not_curtailed() -> None:
    pv = sunny(DAY, peak_kw=PEAK_KW)
    at_cap = [
        Reading(r.start, r.minutes, CAP_KW, pv.kw_at(r.start + timedelta(minutes=2.5)))
        for r in readings(CAP_KW, 0.0, range(11, 14))
    ]

    assert curtailed(at_cap, pv, CAP_KW).kwh == 0.0


def test_no_cap_means_no_curtailment() -> None:
    pv = sunny(DAY, peak_kw=PEAK_KW)

    assert curtailed(readings(CAP_KW, 1.0, range(11, 14)), pv, 0.0).kwh == 0.0
