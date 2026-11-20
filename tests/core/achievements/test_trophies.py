"""The trophy room as a whole: XP adds up, recaps by month, hidden stays hidden."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from custom_components.solar_energy_insights.core.achievements.badges import (
    CROWN_XP,
    TIER_XP,
)
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


def test_xp_is_scores_plus_tiers_and_crowns_plus_quests_met() -> None:
    quest = Quest("fill-x", END, "fill_before", "Fill", 700, 60, END, result=True)
    missed = replace(quest, id="fill-y", result=False)
    # A bigger battery since last winter: every night lasts now.
    facts = [
        replace(f, night_lasted=True, night_spare_kwh=3.0, import_kwh=0.0)
        if f.day > END - timedelta(days=300)
        else f
        for f in home(750, END, varied=True)
    ]
    t = compute(facts, profile_of(facts, 10.0, 52.5), TZ, END, (quest, missed))

    crowns = sum(b.crowns for b in t.badges)
    expected = (
        sum(s for _, s in t.scores)
        + sum(sum(TIER_XP[: b.tier]) for b in t.badges)
        + CROWN_XP * crowns
        + 60
    )
    assert crowns
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


def test_with_prices_a_month_says_what_the_sun_was_worth() -> None:
    _, t = room()

    month = as_dict(t, END, prices=(0.32, 0.08))["months"][-2]

    assert month["saved"] == round(month["kept_kwh"] * 0.32, 2)
    assert month["earned"] == round(month["export_kwh"] * 0.08, 2)
    assert "saved" not in as_dict(t, END)["months"][-2]


def test_a_laddered_badge_shows_each_step() -> None:
    _facts, t = room(days=400)

    warrior = next(b for b in as_dict(t, END)["badges"] if b["id"] == "winter_warrior")

    assert [s["label"] for s in warrior["steps"]] == ["80 %", "85 %", "90 %"]
    assert [s["need"] for s in warrior["steps"]] == warrior["tiers"]
    assert warrior["steps"][0]["days"] >= warrior["steps"][2]["days"]


def test_a_seasonal_badge_shows_this_edition_against_the_last() -> None:
    _facts, t = room(days=750)
    shown = {b["id"]: b for b in as_dict(t, END)["badges"]}

    edition = shown["harvest_night"]["edition"]
    # 31 October: in the middle of the 2026 autumn.
    assert edition["label"] == "2026"
    assert edition["active"]
    assert edition["previous"]["label"] == "2025"
    # A crown asks for at least two more days than last autumn's.
    assert edition["beat"] >= edition["previous"]["days"] + 2
    # Badges played all year have no editions.
    assert shown["every_drop"]["edition"] is None


def test_a_locked_successor_says_what_it_follows() -> None:
    facts = home(200, END, varied=True, tweak=lambda f: replace(f, night_lasted=False))
    t = compute(facts, profile_of(facts, 10.0, 52.5), TZ, END)

    moon = next(b for b in as_dict(t, END)["badges"] if b["id"] == "harvest_moon")

    assert moon["locked"]
    assert moon["follows"] == "harvest_night"
