"""Many runs at once give exactly what one run at a time gives."""

from __future__ import annotations

import itertools
from datetime import timedelta

import numpy as np
import pytest

from custom_components.solar_energy_insights.core.batch import (
    Slices,
    run,
    weighted_quantile,
)
from custom_components.solar_energy_insights.core.battery import Battery, simulate
from tests.core.helpers import at
from tests.core.scenarios import HOUSES, SEASONS, SKIES, history, sky

PAST = 5


@pytest.mark.parametrize(
    ("season", "sky_name", "start_hour", "minute"),
    list(itertools.product(SEASONS, SKIES, (3, 11, 19, 22), (0, 17))),
    ids=str,
)
def test_batch_runs_match_single_runs(season, sky_name, start_hour, minute) -> None:
    start = at(season.day, start_hour, minute)
    sunrise = season.sunrise_after(start)
    sunset = season.sunset_after(start)
    mark = sunset if sunset < sunrise else None
    pv = sky(season, SKIES[sky_name])
    battery = Battery(capacity_kwh=10.0, soc_pct=50.0, floor_pct=7.0, max_power_kw=4.0)
    slices = Slices.between(start, sunrise, mark)
    sun = slices.sample(pv.kw_at)
    socs, rates = [], []
    for (_name, house), soc in itertools.product(
        HOUSES.items(), (3.0, 30.0, 80.0, 100.0)
    ):
        use = history(season.day, house, count=PAST)

        def rate(t, use=use):
            local = t.astimezone(season.sunrise_after(t).tzinfo)
            return use.get((local.date() - timedelta(days=1), local.hour), 0.4)

        socs.append(soc)
        rates.append(rate)
    loads = np.array([slices.sample(r) for r in rates])

    batch = run(battery, socs, slices, sun, loads)

    for k, (soc, rate) in enumerate(zip(socs, rates, strict=True)):
        one = simulate(
            Battery(10.0, soc, 7.0, max_power_kw=4.0),
            start,
            sunrise,
            pv.kw_at,
            rate,
            mark,
        )
        assert bool(batch.lasts[k]) == one.night_lasts
        assert batch.grid_kwh[k] == pytest.approx(one.night_grid_kwh, abs=1e-9)
        assert batch.load_kwh[k] == pytest.approx(one.night_load_kwh, abs=1e-9)
        assert batch.spare_kwh[k] == pytest.approx(one.spare_kwh, abs=1e-9)
        if one.night_reserve_at is None:
            assert batch.reserve_at[k] is None
        else:
            reserve_at = batch.reserve_at[k]
            assert reserve_at is not None
            assert abs((reserve_at - one.night_reserve_at).total_seconds()) < 1e-3


def test_weighted_quantiles_follow_the_weights() -> None:
    values = np.array([3.0, 1.0, 2.0])

    assert weighted_quantile(values, np.ones(3), 0.5) == 2.0
    assert weighted_quantile(values, np.array([0.0, 0.0, 1.0]), 0.5) == 2.0
    assert weighted_quantile(values, np.array([8.0, 1.0, 1.0]), 0.8) == 3.0


def test_recorded_levels_match_the_single_run_curve() -> None:
    season = SEASONS[1]
    start = at(season.day, 20)
    sunrise = season.sunrise_after(start)
    battery = Battery(capacity_kwh=10.0, soc_pct=60.0, floor_pct=5.0)
    slices = Slices.between(start, sunrise, None)
    use = history(season.day, HOUSES["erratic"], count=2)

    def rate(t):
        local = t.astimezone(season.sunrise_after(t).tzinfo)
        return use.get((local.date() - timedelta(days=1), local.hour), 0.4)

    runs = run(
        battery,
        [60.0],
        slices,
        np.zeros(len(slices)),
        np.array([slices.sample(rate)]),
        record=True,
    )
    one = simulate(battery, start, sunrise, lambda _t: 0.0, rate)

    assert runs.levels_pct is not None
    assert runs.levels_pct.shape == (1, len(slices) + 1)
    assert list(runs.levels_pct[0]) == pytest.approx([pct for _, pct in one.points])
