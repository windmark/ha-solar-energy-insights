"""Quests: sized from your own similar days, judged on the day."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from custom_components.solar_energy_insights.core.achievements.profile import profile_of
from custom_components.solar_energy_insights.core.achievements.quests import (
    MAX_LEVEL,
    START_LEVEL,
    Quest,
    judge,
    next_day,
    quest_level,
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


def judged(*results):
    day = date(2026, 10, 1)
    return [
        Quest(
            f"q{k}",
            day + timedelta(days=k),
            "fill_before",
            "",
            0,
            50,
            day + timedelta(days=k),
            result,
        )
        for k, result in enumerate(results)
    ]


def test_quests_start_at_level_two_and_follow_how_you_do() -> None:
    assert quest_level([]) == START_LEVEL
    assert quest_level(judged(True)) == START_LEVEL + 1
    assert quest_level(judged(True, True, False)) == START_LEVEL + 1
    # Open quests do not count either way.
    assert quest_level(judged(None, None)) == START_LEVEL


def test_the_level_stays_between_one_and_the_top() -> None:
    assert quest_level(judged(*[True] * 20)) == MAX_LEVEL
    assert quest_level(judged(*[False] * 20)) == 1
    # From the top, one miss is one step down.
    assert quest_level(judged(*[True] * 20, False)) == MAX_LEVEL - 1


def test_a_higher_level_sets_a_harder_target_for_more_xp() -> None:
    facts, profile = setup(300)
    day = END + timedelta(days=1)
    for forecast in (5.0, 30.0, 60.0):
        easy = next_day(facts, profile, TZ, day, forecast, level=1)
        hard = next_day(facts, profile, TZ, day, forecast, level=MAX_LEVEL)
        assert easy is not None
        assert hard is not None
        assert (easy.level, hard.level) == (1, MAX_LEVEL)
        assert hard.reward >= easy.reward
        if easy.kind == hard.kind == "fill_before":
            assert hard.target <= easy.target  # full earlier
        elif easy.kind == hard.kind:
            assert hard.target >= easy.target


def test_met_quests_make_about_half_of_them_met() -> None:
    """Played forward, the level settles where about half the quests are met:
    harder than the start, where three in five would be."""
    facts, profile = setup(400)
    played: list[Quest] = []
    for k in range(200, 400):
        level = quest_level(played)
        quest = next_day(
            facts[:k], profile, TZ, facts[k].day, facts[k].forecast_kwh, level=level
        )
        assert quest is not None
        result = judge(quest, facts, TZ)
        if result is not None:
            played.append(replace(quest, result=result))
    met = sum(1 for q in played if q.result) / len(played)
    assert len(played) > 100
    assert 0.35 <= met <= 0.65


def test_the_weeks_quest_asks_more_nights_at_a_higher_level() -> None:
    facts, profile = setup()
    monday = END + timedelta(days=(7 - END.weekday()) % 7 or 7)

    low = this_week(facts, profile, monday, level=1)
    high = this_week(facts, profile, monday, level=MAX_LEVEL)

    assert low is not None
    assert high is not None
    assert low.target < high.target <= 7
    assert high.reward > low.reward
