"""Editions: each window of the year plays a seasonal badge against the last."""

from __future__ import annotations

from datetime import date, timedelta

from custom_components.solar_energy_insights.core.achievements.editions import (
    editions,
    in_season,
    label,
    to_beat,
    window,
)


def test_a_window_is_the_one_now_or_the_next() -> None:
    before = window("late autumn", date(2026, 11, 2), southern=False)
    during = window("late autumn", date(2026, 11, 20), southern=False)

    # The six weeks before the winter solstice: next, then now.
    assert before == (date(2026, 11, 9), date(2026, 12, 21))
    assert during == before
    summer_south = window("summer", date(2026, 11, 2), southern=True)
    assert summer_south is not None
    assert summer_south[0] == date(2026, 12, 21)


def test_a_badge_without_a_season_is_always_in_season() -> None:
    assert in_season(None, date(2026, 1, 1), southern=False)
    assert not in_season("summer", date(2026, 1, 1), southern=False)
    assert in_season("summer", date(2026, 1, 1), southern=True)


def test_an_edition_across_new_year_is_named_for_both() -> None:
    assert label(date(2026, 12, 21), date(2027, 3, 19)) == "2026/27"
    assert label(date(2026, 6, 21), date(2026, 9, 21)) == "2026"


def winters(*counts: int) -> list[tuple[date, int]]:
    """Earning days, *counts[k]* of them early in the winter starting in 2023 + k."""
    out = []
    for k, n in enumerate(counts):
        start = date(2023 + k, 12, 22)
        out += [(start + timedelta(days=d), 1) for d in range(n)]
    return out


def every_day(first: date, last: date) -> list[date]:
    """Days a badge could be judged on: all of them."""
    return [first + timedelta(days=k) for k in range((last - first).days + 1)]


def play(earned, first, today, season="winter", judged=None, crowns=True):
    return editions(
        season,
        earned,
        every_day(first, today) if judged is None else judged,
        first,
        today,
        False,
        crowns=crowns,
    )


def test_a_crown_asks_for_clearly_more_than_last_season() -> None:
    played = play(winters(10, 11, 13), date(2023, 12, 1), date(2026, 3, 1))

    assert [e.label for e in played] == ["2023/24", "2024/25", "2025/26"]
    assert [e.days for e in played] == [10, 11, 13]
    # One day more is luck; two more, and a tenth more of the days, is a crown.
    assert played[1].bar == 12
    assert played[1].crowned_on is None
    assert played[2].bar == 13
    assert played[2].crowned_on == date(2025, 12, 22) + timedelta(days=12)


def test_a_partial_first_edition_is_no_bar_to_beat() -> None:
    # History begins in the middle of a winter.
    played = play(winters(0, 5, 9), date(2024, 2, 1), date(2026, 3, 1))

    assert played[0].partial
    assert played[0].bar is None
    assert played[1].bar is None
    assert played[2].crowned_on is not None


def test_a_season_with_days_missing_is_no_bar_to_beat() -> None:
    first, today = date(2023, 12, 1), date(2026, 3, 1)
    # Half of the 2024/25 winter has no data.
    gap = set(every_day(date(2025, 1, 1), date(2025, 2, 15)))
    judged = [d for d in every_day(first, today) if d not in gap]

    played = play(winters(10, 2, 30), first, today, judged=judged)

    assert played[1].partial
    assert played[2].bar is None
    assert played[2].crowned_on is None


def test_the_edition_now_is_the_last_one_even_out_of_season() -> None:
    played = play([], date(2025, 1, 15), date(2026, 6, 1), season="midwinter")

    # The last window opened in December 2025; the next opens later.
    assert played[-1].start == date(2025, 12, 11)
    assert played[-1].end == date(2025, 12, 31)


def test_beating_an_edition_that_earned_nothing_is_no_crown() -> None:
    played = play(winters(0, 3), date(2023, 12, 1), date(2025, 3, 1))

    assert played[1].days == 3
    assert played[1].bar is None
    assert played[1].crowned_on is None
    assert to_beat(played[0], 89) is None
    assert to_beat(played[1], 89) == 5


def test_badges_ranked_against_your_own_days_earn_no_crown() -> None:
    played = play(winters(10, 30), date(2023, 12, 1), date(2025, 3, 1), crowns=False)

    assert played[1].bar is None
    assert played[1].crowned_on is None


def test_a_bar_past_what_the_season_allows_is_no_bar() -> None:
    # 85 of 89 days last winter: 10 % more doesn't fit in a winter.
    played = play(winters(85, 87), date(2023, 12, 1), date(2025, 3, 1))

    assert played[1].bar is None
