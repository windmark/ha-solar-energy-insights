"""Each string's share of the solar counter."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.solar_energy_insights.core.split import split

T = datetime(2026, 6, 1, 10, tzinfo=UTC)
H = timedelta(hours=1)


def test_the_strings_add_up_to_the_counter_in_their_shares() -> None:
    got = split({T: 4.8}, {1: {T: 3.0}, 2: {T: 1.0}, 3: {T: 1.0}})

    assert sum(got[n][T] for n in got) == pytest.approx(4.8)
    assert got[1][T] == pytest.approx(4.8 * 3 / 5)
    assert got[3][T] == pytest.approx(4.8 / 5)


def test_without_a_counter_reading_the_power_stands() -> None:
    got = split({T: 2.0}, {1: {T: 1.0, T + H: 1.5}, 2: {T: 1.0, T + H: 0.5}})

    assert got[1][T + H] == pytest.approx(1.5)
    assert got[2][T + H] == pytest.approx(0.5)


def test_a_string_without_a_reading_does_not_hand_its_share_to_the_others() -> None:
    got = split({T: 3.0}, {1: {T: 1.0}, 2: {}})

    assert got[1][T] == pytest.approx(1.0)


def test_no_power_means_nothing_to_share() -> None:
    got = split({T: 0.2}, {1: {T: 0.0}, 2: {T: -0.01}})

    assert got[1][T] == 0
    assert got[2][T] == 0
