"""Moments: each says what was achieved and why, and is kept for two days."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from custom_components.solar_energy_insights.core.achievements import moments
from custom_components.solar_energy_insights.core.achievements.badges import (
    CATALOGUE,
    BadgeState,
)
from custom_components.solar_energy_insights.core.achievements.game import (
    Level,
    Record,
)
from custom_components.solar_energy_insights.core.achievements.quests import Quest

NOW = datetime(2026, 11, 15, 0, 10, tzinfo=UTC)
PERFECT = next(b for b in CATALOGUE if b.id == "perfect_day")


def test_a_badge_says_what_earned_it_and_what_is_next() -> None:
    state = BadgeState(PERFECT, 1, 1, 5, 0.04, (date(2026, 11, 14),), ())

    m = moments.badge(state)

    assert m["title"] == "Perfect day: bronze"
    assert m["explain"] == (
        "Grid-free, battery full and the morning forecast beaten. "
        "1 of 5 for silver. Rare: 4 % of your days earn it."
    )


def test_a_complete_badge_says_so() -> None:
    state = BadgeState(PERFECT, 20, 3, None, 0.3, (), ())

    assert "the badge is complete" in moments.badge(state)["explain"]


def test_a_record_reads_as_people_read_it() -> None:
    earliest = Record(
        "earliest_full",
        "Earliest full",
        "mdi:x",
        "min",
        612,
        date(2026, 11, 14),
        None,
        None,
    )

    m = moments.record(earliest, date(2025, 1, 3))

    assert m["message"] == "New record, Earliest full: 10:12."
    assert m["explain"] == (
        "10:12 on Saturday 14 Nov, the best of all your days since 3 Jan 2025."
    )


def test_a_level_says_how_far_the_next_one_is() -> None:
    m = moments.level(Level(7, "Photon", 2830, 2800, 3200))

    assert m["title"] == "Level 7: Photon"
    assert m["explain"].endswith("370 XP to level 8.")


def test_a_weeks_quest_is_called_one() -> None:
    week = Quest(
        "w",
        date(2026, 11, 9),
        "nights",
        "Five grid-free nights",
        5,
        80,
        date(2026, 11, 15),
        result=True,
    )

    assert moments.quest(week)["explain"].startswith("This week's quest, met: +80 XP")


def test_moments_are_kept_two_days_newest_first() -> None:
    first = moments.stamp([{"kind": "record"}], NOW - timedelta(hours=30))
    old = moments.stamp([{"kind": "level"}], NOW - timedelta(days=3))
    both = moments.stamp([{"kind": "level"}, {"kind": "badge"}], NOW)

    kept = moments.recent([*first, *old, *both, {"kind": "broken"}], NOW)

    assert [m["kind"] for m in kept] == ["level", "badge", "record"]
    assert len({m["id"] for m in kept}) == 3
