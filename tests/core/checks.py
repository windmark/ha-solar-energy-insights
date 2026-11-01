"""Properties every plan must have, as reusable checks.

The invariant tests run them over a grid of made-up situations; the replay
tests run them over real moments saved from diagnostics.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from custom_components.solar_energy_insights.core.plan import Plan, PlanInputs

EPS = 1e-6

type Check = Callable[[str, PlanInputs, Plan], None]


def night_start(i: PlanInputs) -> datetime:
    return i.sunset if i.sunset < i.sunrise else i.now


def heavy_never_ends_better(name: str, _i: PlanInputs, p: Plan) -> None:
    if p.night is None:
        return
    typical, heavy = p.night.typical, p.night.heavy
    assert heavy.spare_kwh <= typical.spare_kwh + EPS, name
    assert heavy.grid_kwh >= typical.grid_kwh - EPS, name


def nights_are_consistent(name: str, i: PlanInputs, p: Plan) -> None:
    if p.night is None:
        return
    for o in p.night.outcomes:
        if o.lasts:
            assert o.reserve_at is None, name
            assert o.grid_kwh <= EPS, name
        else:
            assert o.reserve_at is not None, name
            assert o.grid_kwh > 0, name
            assert o.spare_kwh <= EPS, name
            # Never this morning, never after sunrise.
            assert night_start(i) <= o.reserve_at <= i.sunrise, name
        assert o.spare_kwh >= 0, name
        assert o.load_kwh >= 0, name


def shares_and_counts_agree(name: str, _i: PlanInputs, p: Plan) -> None:
    if p.night is None:
        return
    assert 0 <= p.night.share <= 1, name
    assert 0 <= p.night.lasting <= len(p.night.outcomes), name
    assert abs(p.night.share - p.night.lasting / len(p.night.outcomes)) < EPS, name


def charge_needed_is_a_level(name: str, i: PlanInputs, p: Plan) -> None:
    needed = p.needed
    if needed is None:
        return
    assert needed.at == night_start(i), name
    for pct in (needed.typical_pct, needed.heavy_pct):
        if pct is not None:
            assert i.battery.floor_pct - EPS <= pct <= i.battery.ceiling_pct + EPS, name
    if needed.heavy_pct is not None:
        assert needed.typical_pct is not None, name
        assert needed.heavy_pct >= needed.typical_pct - EPS, name


def the_day_stays_within_the_day(name: str, i: PlanInputs, p: Plan) -> None:
    charge = p.charge
    if charge.full_at is not None:
        assert i.now <= charge.full_at <= i.sunset, name
    if charge.full_at_cloudy is not None:
        assert charge.full_at is not None, name
        assert charge.full_at_cloudy >= charge.full_at, name
    top = max(i.battery.ceiling_pct, i.battery.soc_pct)
    assert min(i.battery.soc_pct, 100) - 0.5 <= charge.peak_pct <= top + EPS, name
    assert charge.peak_pct_cloudy <= charge.peak_pct + EPS, name
    if charge.sunset_pct is not None:
        assert 0 <= charge.sunset_pct <= 100 + EPS, name
    assert all(0 <= pct <= top + EPS for _, pct in p.curve), name


def surplus_is_sane(name: str, _i: PlanInputs, p: Plan) -> None:
    assert p.surplus.after_full_kwh >= 0, name
    assert p.surplus.while_charging_kwh >= 0, name
    assert p.surplus.cloudy_kwh <= p.surplus.total_kwh + EPS, name


def windows_are_ahead_and_in_daylight(name: str, i: PlanInputs, p: Plan) -> None:
    window = p.best_window
    if window is not None:
        assert window.start < window.end <= i.sunset, name
        assert window.end > i.now, name
        assert window.kwh > 0, name
    if p.free_until is not None:
        assert p.free_power, name
        assert p.free_until > i.now, name


def the_days_working_adds_up(name: str, i: PlanInputs, p: Plan) -> None:
    day = p.day
    assert day is not None, name
    start = i.now if i.sunset < i.sunrise else i.sunrise
    for t, pv, load in day.slots:
        assert start - timedelta(minutes=30) < t < i.sunset, name
        assert pv >= 0, name
        assert load >= 0, name
    # Less sun never leaves the battery fuller.
    expected = dict(p.curve)
    for t, pct in day.curve_cloudy:
        if t in expected:
            assert pct <= expected[t] + EPS, name
    if day.reserve_at is not None:
        assert i.now <= day.reserve_at <= i.sunset, name
    # The best window is made of half hours that really have sun to spare.
    window = p.best_window
    if window is not None:
        spare = {t: pv - load for t, pv, load in day.slots}
        inside = [v for t, v in spare.items() if window.start <= t < window.end]
        assert inside, name
        assert all(v >= 1.0 - EPS for v in inside), name


def the_nights_budget_adds_up(name: str, _i: PlanInputs, p: Plan) -> None:
    night = p.night
    if night is None or night.budget_kwh is None:
        return
    assert night.budget_kwh >= 0, name
    assert night.evening_budget_kwh is not None, name
    assert 0 <= night.evening_budget_kwh <= night.budget_kwh + EPS, name
    for o in night.outcomes:
        # The evening and the rest of the night are the whole night's use.
        assert (
            abs(o.evening_kwh + o.late_kwh - o.load_kwh) <= 0.05 * o.load_kwh + 0.05
        ), name


ALL: tuple[Check, ...] = (
    the_nights_budget_adds_up,
    the_days_working_adds_up,
    heavy_never_ends_better,
    nights_are_consistent,
    shares_and_counts_agree,
    charge_needed_is_a_level,
    the_day_stays_within_the_day,
    surplus_is_sane,
    windows_are_ahead_and_in_daylight,
)


def check_all(name: str, i: PlanInputs, p: Plan) -> None:
    for check in ALL:
        check(name, i, p)
