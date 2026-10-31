"""Quests: sized from your own similar days, judged on the day."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from custom_components.solar_energy_insights.core.achievements.profile import profile_of
from custom_components.solar_energy_insights.core.achievements.quests import (
    judge,
    next_day,
    this_week,
)
from tests.core.achievements.homes import home
from tests.core.helpers import TZ

END = date(2026, 10, 31)


def setup(days=120):
    facts = home(days, END, varied=True)
    return facts, profile_of(facts, 10.0, 52.5)


def test_a_borderline_night_comes_first() -> None:
    facts, profile = setup()

    quest = next_day(facts, profile, TZ, END + timedelta(days=1), 20.0, "borderline")

    assert quest is not None
    assert quest.kind == "grid_free_night"
    assert quest.day == END


def test_a_quest_is_met_on_about_three_in_five_similar_days() -> None:
    facts, profile = setup(300)
    met = tried = 0
    for k in range(200, 300):
        day = facts[k].day
        quest = next_day(facts[:k], profile, TZ, day, facts[k].forecast_kwh)
        assert quest is not None
        assert 25 <= quest.reward <= 100
        result = judge(quest, facts, TZ)
        if result is not None:
            tried += 1
            met += result
    assert tried > 50
    assert 0.45 <= met / tried <= 0.75


def test_each_kind_is_judged_on_its_fact() -> None:
    facts, profile = setup()
    day = END + timedelta(days=1)
    for forecast in (5.0, 30.0, 60.0):
        quest = next_day(facts, profile, TZ, day, forecast)
        assert quest is not None
        assert judge(quest, facts, TZ) is None  # the day has not happened
        tomorrow = replace(facts[-1], day=day)
        assert judge(quest, [*facts, tomorrow], TZ) in (True, False)


def test_the_weeks_quest_counts_grid_free_nights() -> None:
    facts, profile = setup()
    monday = END + timedelta(days=(7 - END.weekday()) % 7 or 7)

    quest = this_week(facts, profile, monday)

    assert quest is not None
    week = [
        replace(facts[-1], day=monday + timedelta(days=k), night_lasted=True)
        for k in range(7)
    ]
    assert judge(quest, [*facts, *week], TZ) is True
    short = [replace(f, night_lasted=False) for f in week]
    assert judge(quest, [*facts, *short], TZ) is False
