"""What must hold for every plan, in every situation in the grid.

These are the kinds of mistakes found by looking at a real morning: a heavy
night ending better than a typical one, or this morning's low battery counted
as a failed night. Rather than wait for the next one on a real day, every
combination of season, sky, house, battery level and time is checked.
"""

from __future__ import annotations

import itertools
from datetime import datetime

import pytest

from custom_components.solar_energy_insights.core.plan import Plan, PlanInputs, plan
from tests.core.scenarios import HOUSES, SEASONS, SKIES, history, inputs, moments

EPS = 1e-6
SOCS = (6.0, 50.0, 100.0)


def cases() -> list[tuple[str, PlanInputs]]:
    out = []
    for season, (sky_name, peak), (house_name, house), soc in itertools.product(
        SEASONS, SKIES.items(), HOUSES.items(), SOCS
    ):
        use = history(season.day, house)
        for now in moments(season):
            name = f"{season.name}-{sky_name}-{house_name}-{soc:.0f}%-{now:%H:%M}"
            out.append((name, inputs(season, now, peak_kw=peak, use=use, soc=soc)))
    return out


CASES = cases()


@pytest.fixture(scope="module")
def plans() -> list[tuple[str, PlanInputs, Plan]]:
    return [(name, i, plan(i)) for name, i in CASES]


def night_start(i: PlanInputs) -> datetime:
    return i.sunset if i.sunset < i.sunrise else i.now


def test_the_grid_is_big_enough_to_mean_something(plans) -> None:
    assert len(plans) > 600


def test_a_heavy_night_never_ends_better_than_a_typical_one(plans) -> None:
    for name, _, p in plans:
        assert p.night is not None, name
        typical, heavy = p.night.typical, p.night.heavy
        assert heavy.spare_kwh <= typical.spare_kwh + EPS, name
        assert heavy.grid_kwh >= typical.grid_kwh - EPS, name


def test_each_night_either_lasts_or_says_when_and_how_much(plans) -> None:
    for name, i, p in plans:
        assert p.night is not None
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


def test_shares_and_counts_agree(plans) -> None:
    for name, _, p in plans:
        assert p.night is not None
        assert 0 <= p.night.share <= 1, name
        assert 0 <= p.night.lasting <= len(p.night.outcomes), name
        # Equal weights: the share is the count.
        assert p.night.share == pytest.approx(
            p.night.lasting / len(p.night.outcomes)
        ), name


def test_charge_needed_is_a_level_and_heavy_needs_at_least_typical(plans) -> None:
    for name, i, p in plans:
        needed = p.needed
        assert needed is not None, name
        assert needed.at == night_start(i), name
        for pct in (needed.typical_pct, needed.heavy_pct):
            if pct is not None:
                assert i.battery.floor_pct - EPS <= pct <= 100 + EPS, name
        if needed.heavy_pct is not None:
            assert needed.typical_pct is not None, name
            assert needed.heavy_pct >= needed.typical_pct - EPS, name


def test_the_day_stays_within_the_day(plans) -> None:
    for name, i, p in plans:
        charge = p.charge
        if charge.full_at is not None:
            assert i.now <= charge.full_at <= i.sunset, name
        if charge.full_at_cloudy is not None:
            assert charge.full_at is not None, name
            assert charge.full_at_cloudy >= charge.full_at, name
        assert i.battery.soc_pct - 0.5 <= charge.peak_pct <= 100 + EPS, name
        assert charge.peak_pct_cloudy <= charge.peak_pct + EPS, name
        if charge.sunset_pct is not None:
            assert 0 <= charge.sunset_pct <= 100 + EPS, name
        assert all(0 <= pct <= 100 + EPS for _, pct in p.curve), name


def test_surplus_is_never_negative_and_clouds_never_add_to_it(plans) -> None:
    for name, _, p in plans:
        assert p.surplus.after_full_kwh >= 0, name
        assert p.surplus.while_charging_kwh >= 0, name
        assert p.surplus.cloudy_kwh <= p.surplus.total_kwh + EPS, name


def test_windows_and_free_power_are_in_the_future_and_in_daylight(plans) -> None:
    for name, i, p in plans:
        window = p.best_window
        if window is not None:
            assert window.start < window.end <= i.sunset, name
            assert window.end > i.now, name
            assert window.kwh > 0, name
        if p.free_until is not None:
            assert p.free_power, name
            assert p.free_until > i.now, name
