"""The array's size and where it faces, from Solcast's sites."""

from __future__ import annotations

import pytest

from custom_components.solar_energy_insights.core.array import (
    Array,
    Plane,
    from_sites,
    plane_yield,
)

BERLIN, SYDNEY = 52.5, -33.9


def test_facing_the_equator_at_a_good_tilt_is_ideal() -> None:
    assert plane_yield(180, 35, BERLIN) == pytest.approx(1.0, abs=0.03)
    assert plane_yield(0, 30, SYDNEY) == pytest.approx(1.0, abs=0.03)


# The ranges are PVGIS's yields for central Europe, give or take a few points.
@pytest.mark.parametrize(
    ("compass", "tilt", "low", "high"),
    (
        (90, 30, 0.78, 0.88),  # east
        (270, 30, 0.78, 0.88),  # west
        (0, 30, 0.52, 0.68),  # north, away from the sun in Berlin
        (180, 0, 0.83, 0.93),  # flat
        (180, 90, 0.65, 0.78),  # a south wall
        (135, 30, 0.93, 1.0),  # south-east
    ),
)
def test_other_directions_make_less_per_kwp(compass, tilt, low, high) -> None:
    assert low <= plane_yield(compass, tilt, BERLIN) <= high


def test_east_and_west_mirror_each_other() -> None:
    assert plane_yield(90, 30, BERLIN) == pytest.approx(
        plane_yield(270, 30, BERLIN), abs=0.01
    )


def test_in_the_south_the_sun_is_to_the_north() -> None:
    assert plane_yield(0, 30, SYDNEY) > plane_yield(180, 30, SYDNEY)


def test_an_array_adds_its_planes_and_weighs_their_yield_by_size() -> None:
    array = Array((Plane(10, 90, 30), Plane(5, 270, 30)), BERLIN)

    assert array.kwp == 15
    assert array.relative_yield == pytest.approx(plane_yield(90, 30, BERLIN), abs=0.01)


def test_planes_come_from_solcast_sites() -> None:
    sites = [
        {
            "name": "East",
            "capacity": 6.0,
            "capacity_dc": 7.6,
            "compass_degrees": 90,
            "tilt": 25,
        },
        {
            "name": "West",
            "capacity": 6.0,
            "capacity_dc": 7.6,
            "azimuth": 90,
            "tilt": 25,
        },
    ]

    array = from_sites(sites, BERLIN)

    assert array.kwp == pytest.approx(15.2)
    assert [p.compass_deg for p in array.planes] == [90, 270]
    assert 0.78 < array.relative_yield < 0.88


def test_a_size_set_by_hand_wins_and_nothing_is_guessed() -> None:
    assert Array((), BERLIN, kwp_override=12.0).kwp == 12.0
    assert Array((), BERLIN).kwp is None
    assert Array((), BERLIN).relative_yield == 1.0
