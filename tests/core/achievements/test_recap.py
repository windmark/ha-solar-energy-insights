"""Quests with the plan's advice; last week in one line; best since; a year ago."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from custom_components.solar_energy_insights.core.achievements.facts import DayFacts
from custom_components.solar_energy_insights.core.achievements.quests import (
    Advice,
    Quest,
)
from custom_components.solar_energy_insights.core.achievements.recap import (
    best_since,
    last_week,
    year_ago,
)
from tests.core.achievements.homes import home

TUESDAY = date(2026, 11, 3)


def test_advice_words_the_plans_budget_and_window() -> None:
    a = Advice(evening_budget_kwh=5.68, window_hours=("10:00", "14:00"))

    assert a.evening() == "keep the rest of the evening under ~5.7 kWh"
    assert a.window() == "run heavy loads 10:00–14:00"
    assert Advice().evening() is None
    assert Advice().window() is None
    assert Advice(evening_budget_kwh=0.0).evening() is None  # nothing left: no advice


def test_a_quest_kept_before_hints_existed_still_loads() -> None:
    old = {
        "id": "fill-x",
        "day": "2026-11-03",
        "kind": "fill_before",
        "title": "Fill",
        "target": 720.0,
        "reward": 60,
        "until": "2026-11-03",
        "result": None,
    }

    assert Quest.from_dict(old).hint is None
    new = Quest.from_dict({**old, "hint": "run heavy loads 10:00–14:00"})
    assert Quest.from_dict(new.as_dict()) == new


def day(
    d: date,
    solar: float,
    export: float,
    lasted: bool | None,
    house: float = 10.0,
    imported: float = 1.0,
) -> DayFacts:
    return DayFacts(
        day=d,
        house_kwh=house,
        solar_kwh=solar,
        export_kwh=export,
        import_kwh=imported,
        night_lasted=lasted,
    )


def test_last_week_monday_to_sunday() -> None:
    monday = date(2026, 10, 26)
    week = [
        day(monday + timedelta(days=k), 20.0 + k, 5.0, k % 3 != 0) for k in range(7)
    ]
    other = [
        day(monday - timedelta(days=1), 99.0, 0.0, True),
        day(TUESDAY, 99.0, 0.0, True),
    ]

    r = last_week(
        week + other,
        TUESDAY,
        price=0.3,
        earned=[("Night owl", monday), ("Old", monday - timedelta(days=9))],
    )

    assert r is not None
    assert (r["from"], r["until"]) == ("2026-10-26", "2026-11-01")
    assert (r["nights"], r["nights_lasted"]) == (7, 4)
    assert (r["best_day"], r["best_kwh"]) == ("2026-11-01", 26.0)
    assert r["kept_kwh"] == pytest.approx(7 * 23.0 - 35.0)
    assert r["saved"] == pytest.approx((7 * 23.0 - 35.0) * 0.3)
    assert r["badges"] == ["Night owl"]


def test_no_recap_for_a_week_with_too_few_days() -> None:
    assert last_week([day(date(2026, 10, 27), 20.0, 5.0, True)], TUESDAY) is None


def history(n: int, solar: float = 20.0) -> list[DayFacts]:
    start = TUESDAY - timedelta(days=n)
    return [day(start + timedelta(days=k), solar, 5.0, True) for k in range(n)]


def test_best_since_names_the_last_day_as_good_and_the_longest_gaps_first() -> None:
    days = history(40)
    days[10] = day(days[10].day, 30.0, 5.0, True, house=8.0)  # as good, 30 days back
    yesterday = TUESDAY - timedelta(days=1)
    days = [f for f in days if f.day != yesterday] + [
        day(yesterday, 29.0, 5.0, True, house=8.5)
    ]

    found = best_since(days, yesterday)

    assert found[0]["key"] == "best_day"
    assert found[0]["since"] == days[10].day.isoformat()
    assert found[0]["gap_days"] == (yesterday - days[10].day).days


def test_a_few_days_is_not_news() -> None:
    days = history(40)
    yesterday = TUESDAY - timedelta(days=1)
    days[-3] = day(days[-3].day, 25.0, 5.0, True)  # as good, three days back
    days = [f for f in days if f.day != yesterday] + [day(yesterday, 24.0, 5.0, True)]

    assert all(x["key"] != "best_day" for x in best_since(days, yesterday))


def test_on_record_only_with_a_month_behind_it() -> None:
    yesterday = TUESDAY - timedelta(days=1)
    # history() runs up to and including yesterday: replace it.
    short = [*history(10)[:-1], day(yesterday, 99.0, 5.0, True)]
    long = [*history(40)[:-1], day(yesterday, 99.0, 5.0, True)]

    assert all(x["key"] != "best_day" for x in best_since(short, yesterday))
    assert {"key": "best_day", "since": None}.items() <= best_since(long, yesterday)[
        0
    ].items()


END = date(2026, 10, 31)


def test_the_last_week_is_held_to_the_same_week_a_year_ago() -> None:
    days = home(400, END)
    # A better year: a little less from the grid every day.
    better = [
        replace(f, import_kwh=round((f.import_kwh or 0) * 0.5, 2))
        if f.day > END - timedelta(days=30)
        else f
        for f in days
    ]

    got = year_ago(better, END + timedelta(days=1))

    assert got is not None
    assert got["until"] == END.isoformat()
    assert got["then_until"] == (END - timedelta(days=364)).isoformat()
    rows = {r["key"]: r for r in got["measures"]}
    assert rows["self_sufficiency"]["now"] >= rows["self_sufficiency"]["then"]
    assert rows["self_sufficiency"]["better"] in (True, None)
    # House use is shown, not judged.
    assert rows["house_kwh"]["better"] is None


def test_the_same_weekdays_line_up() -> None:
    got = year_ago(home(400, END), END + timedelta(days=1))

    assert got is not None
    then = date.fromisoformat(got["then_until"])
    assert then.weekday() == END.weekday()


def test_without_last_year_there_is_nothing_to_compare() -> None:
    assert year_ago(home(200, END), END + timedelta(days=1)) is None


def test_today_is_chased_against_the_same_day_last_year() -> None:
    days = home(400, END)

    got = year_ago(days, END)

    assert got is not None
    year_before = next(f for f in days if f.day == END - timedelta(days=364))
    assert got["today_solar_kwh"] == year_before.solar_kwh


def test_a_missing_day_last_year_leaves_that_day_out_of_both_weeks() -> None:
    same = [replace(f, solar_kwh=10.0, house_kwh=12.0) for f in home(400, END)]
    gone = {END - timedelta(days=364 + k) for k in (0, 1)}
    patchy = [f for f in same if f.day not in gone]

    got = year_ago(patchy, END + timedelta(days=1))

    assert got is not None
    assert got["days"] == 5
    rows = {r["key"]: r for r in got["measures"]}
    assert rows["solar_kwh"]["now"] == rows["solar_kwh"]["then"] == 50.0
    assert rows["solar_kwh"]["better"] is None
