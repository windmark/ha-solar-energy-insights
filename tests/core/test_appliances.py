"""When to start an appliance, from the plan."""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.solar_energy_insights.core.appliances import (
    Run,
    best_start,
    learned_run,
)
from custom_components.solar_energy_insights.core.plan import plan
from tests.core.helpers import TZ, at
from tests.core.scenarios import SEASONS, flat, history, inputs

SUMMER, WINTER = SEASONS
DISHWASHER = Run(kwh=1.2, hours=2.0)


def summer(hour: int, soc: float, peak_kw: float = 9.0):
    use = history(SUMMER.day, flat)
    return plan(inputs(SUMMER, at(SUMMER.day, hour), peak_kw=peak_kw, use=use, soc=soc))


def test_with_the_battery_full_at_noon_now_is_a_good_time() -> None:
    start = best_start(summer(12, soc=100), DISHWASHER, TZ)

    assert start is not None
    assert start.free_share == pytest.approx(1.0)
    assert start.now_share == pytest.approx(1.0)
    assert start.good_now
    assert start.at <= at(SUMMER.day, 12)


def test_while_the_battery_still_charges_the_best_start_is_later() -> None:
    p = summer(7, soc=10, peak_kw=6.0)
    start = best_start(p, DISHWASHER, TZ)

    assert start is not None
    assert p.charge.full_at is not None
    assert start.at >= p.charge.full_at - timedelta(minutes=30)
    assert start.now_share is not None
    assert start.now_share < start.free_share
    assert not start.good_now


def test_a_winter_day_has_no_free_sun_to_start_in() -> None:
    use = history(WINTER.day, flat)
    p = plan(inputs(WINTER, at(WINTER.day, 11), peak_kw=1.0, use=use, soc=20))
    start = best_start(p, DISHWASHER, TZ)

    assert start is not None
    assert start.free_share == 0.0
    assert not start.good_now


def test_before_sunrise_there_is_no_now_to_compare_with() -> None:
    start = best_start(summer(3, soc=60), DISHWASHER, TZ)

    assert start is not None
    assert start.now_share is None
    assert not start.good_now
    assert start.at > at(SUMMER.day, 5)


def test_after_sunset_nothing_is_suggested_for_tonight() -> None:
    start = best_start(summer(22, soc=60), DISHWASHER, TZ)

    # The plan's day is tomorrow's: a start tomorrow, and no "now".
    assert start is not None
    assert start.now_share is None
    assert start.at > at(SUMMER.day + timedelta(days=1), 5)


def test_a_bigger_load_gets_a_smaller_share_from_the_same_sun() -> None:
    p = summer(12, soc=100, peak_kw=4.0)
    small = best_start(p, Run(kwh=0.8, hours=1.0), TZ)
    large = best_start(p, Run(kwh=6.0, hours=1.0), TZ)

    assert small is not None
    assert large is not None
    assert large.free_share < small.free_share


def test_a_believable_learned_run_beats_the_typical_one() -> None:
    run = learned_run(1.2, 2.0, kwh=0.9, hours=[1.5, 1.6, 1.7])

    assert run.kwh == 0.9
    assert run.hours == pytest.approx(1.6)
    assert not run.estimated


def test_a_learned_run_out_of_range_falls_back_to_the_typical_one() -> None:
    assert learned_run(1.2, 2.0, kwh=9.0) == Run(1.2, 2.0, estimated=True)
    assert learned_run(1.2, 2.0, kwh=0.01, hours=[0.1, 9.0]).hours == 2.0
    assert learned_run(1.2, 2.0).estimated


def test_only_the_last_few_durations_count() -> None:
    run = learned_run(1.2, 2.0, hours=[5.0] * 10 + [1.0] * 5)

    assert run.hours == pytest.approx(1.0)
