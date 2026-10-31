"""What must hold for every plan, in every situation in the grid.

These are the kinds of mistakes found by looking at a real morning: a heavy
night ending better than a typical one, or this morning's low battery counted
as a failed night. Rather than wait for the next one on a real day, every
combination of season, sky, house, battery level and time is checked.
"""

from __future__ import annotations

import itertools

import pytest

from custom_components.solar_energy_insights.core.plan import Plan, PlanInputs, plan
from tests.core import checks
from tests.core.scenarios import HOUSES, SEASONS, SKIES, history, inputs, moments

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
    # The same seasons, skies, houses and times with charging stopped at 80 %.
    for season, (sky_name, peak), (house_name, house) in itertools.product(
        SEASONS, SKIES.items(), HOUSES.items()
    ):
        use = history(season.day, house)
        for now in moments(season):
            name = f"{season.name}-{sky_name}-{house_name}-limit80-{now:%H:%M}"
            out.append(
                (name, inputs(season, now, peak_kw=peak, use=use, soc=50, ceiling=80))
            )
    return out


@pytest.fixture(scope="module")
def plans() -> list[tuple[str, PlanInputs, Plan]]:
    return [(name, i, plan(i)) for name, i in cases()]


def test_the_grid_is_big_enough_to_mean_something(plans) -> None:
    assert len(plans) > 850
    assert all(p.night is not None and p.needed is not None for _, _, p in plans)


@pytest.mark.parametrize("check", checks.ALL, ids=lambda c: c.__name__)
def test_every_plan_in_the_grid(plans, check: checks.Check) -> None:
    for name, i, p in plans:
        check(name, i, p)
