"""The trophy room as a whole: XP adds up, recaps by month, hidden stays hidden."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from custom_components.solar_energy_insights.core.achievements.badges import TIER_XP
from custom_components.solar_energy_insights.core.achievements.profile import profile_of
from custom_components.solar_energy_insights.core.achievements.quests import Quest
from custom_components.solar_energy_insights.core.achievements.trophies import (
    as_dict,
    compute,
)
from tests.core.achievements.homes import home
from tests.core.helpers import TZ

END = date(2026, 10, 31)


def room(days=200, quests=()):
    facts = home(days, END, varied=True)
    profile = profile_of(facts, 10.0, 52.5)
    return facts, compute(facts, profile, TZ, END, quests)


def test_xp_is_scores_plus_tiers_plus_quests_met() -> None:
    quest = Quest("fill-x", END, "fill_before", "Fill", 700, 60, END, result=True)
    missed = replace(quest, id="fill-y", result=False)
    _facts, t = room(quests=(quest, missed))

    expected = (
        sum(s for _, s in t.scores) + sum(sum(TIER_XP[: b.tier]) for b in t.badges) + 60
    )
    assert t.level.xp == expected


def test_the_same_history_gives_the_same_room() -> None:
    _, first = room()
    _, second = room()

    assert first == second


def test_months_recap_their_days() -> None:
    facts, t = room()

    october = next(m for m in t.months if m.month == "2026-10")
    days = [f for f in facts if f.day.month == 10]
    assert october.days == len(days)
    assert october.nights == sum(1 for f in days if f.night_lasted is not None)
    assert october.best_day_kwh == max(f.solar_kwh for f in days)


def test_hidden_badges_show_a_riddle_until_earned() -> None:
    _facts, t = room()

    shown = as_dict(t, END)
    hidden = [b for b in shown["badges"] if b["hidden"]]
    assert hidden
    for b in hidden:
        if b["count"]:
            assert b["name"]
        else:
            assert b["name"] is None
            assert b["riddle"]
            assert b["icon"] == "mdi:help"


def test_the_room_says_what_season_it_is_where_you_live() -> None:
    _, t = room()
    south = compute(
        home(200, END, southern=True),
        profile_of(home(200, END, southern=True), 10.0, -33.9),
        TZ,
        END,
    )

    assert as_dict(t, END)["profile"]["season"] == "autumn"
    assert as_dict(south, END)["profile"]["season"] == "spring"
