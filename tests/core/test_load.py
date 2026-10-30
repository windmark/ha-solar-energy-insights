"""The house load is learned, and cooking evenings are told apart."""

from __future__ import annotations

from datetime import date, timedelta

from custom_components.solar_energy_insights.core.load import (
    Evening,
    LoadModel,
    cycle_day,
    detect_cooking,
)
from tests.core.helpers import TZ, at

MONDAY = date(2026, 6, 8)


def feed_weeks(model: LoadModel, weeks: int, kwh_at) -> None:
    for day in range(7 * weeks):
        for hour in range(24):
            t = at(MONDAY + timedelta(day), hour)
            model.observe_hour(t, kwh_at(t.astimezone(TZ)), TZ)


def test_learns_a_repeating_week() -> None:
    model = LoadModel()
    pattern = lambda local: 2.0 if local.hour == 19 else 0.3  # noqa: E731

    feed_weeks(model, 2, pattern)
    early = model.mae_kwh
    feed_weeks(model, 6, pattern)

    assert model.rate(at(MONDAY, 19), TZ) > model.rate(at(MONDAY, 3), TZ)
    assert early is not None
    assert model.mae_kwh is not None
    assert model.mae_kwh <= early


def test_ignores_implausible_hours() -> None:
    model = LoadModel()

    model.observe_hour(at(MONDAY, 12), 999.0, TZ)
    model.observe_hour(at(MONDAY, 13), -1.0, TZ)

    assert model.week == LoadModel().week


def test_cooking_evenings_use_more_than_quiet_ones() -> None:
    model = LoadModel()
    quiet = [0.3] * 24
    cook = [0.3] * 24
    cook[18] = cook[19] = 1.5
    for _ in range(4):
        model.observe_evening(quiet, cooked=False)
        model.observe_evening(cook, cooked=True)

    evening = at(MONDAY, 18, 30)
    assert model.rate(evening, TZ, Evening.COOK) > model.rate(
        evening, TZ, Evening.QUIET
    )
    mix = model.rate(evening, TZ, Evening.MIX)
    assert (
        model.rate(evening, TZ, Evening.QUIET)
        < mix
        < model.rate(evening, TZ, Evening.COOK)
    )
    assert model.cook_share == 0.5


def test_assumes_extra_for_cooking_until_one_is_learned() -> None:
    model = LoadModel()
    evening = at(MONDAY, 18)

    assert model.rate(evening, TZ, Evening.COOK) > model.rate(
        evening, TZ, Evening.QUIET
    )


def test_survives_a_round_trip() -> None:
    model = LoadModel()
    feed_weeks(model, 1, lambda _local: 0.5)
    model.observe_evening([0.4] * 24, cooked=True)

    restored = LoadModel.from_dict(model.as_dict())

    assert restored.rate(at(MONDAY, 20), TZ) == model.rate(at(MONDAY, 20), TZ)


def test_a_night_belongs_to_its_evening() -> None:
    assert cycle_day(at(MONDAY, 20), TZ) == MONDAY
    assert cycle_day(at(MONDAY + timedelta(1), 3), TZ) == MONDAY
    assert cycle_day(at(MONDAY + timedelta(1), 13), TZ) == MONDAY + timedelta(1)


def test_cooking_is_a_high_draw_in_the_evening_only() -> None:
    lunch = [(at(MONDAY, 12), 3.0)]
    dinner = [(at(MONDAY, 18), 3.0)]
    simmer = [(at(MONDAY, 18), 1.0)]

    assert not detect_cooking(lunch, TZ, 2.0)
    assert detect_cooking(dinner, TZ, 2.0)
    assert not detect_cooking(simmer, TZ, 2.0)
