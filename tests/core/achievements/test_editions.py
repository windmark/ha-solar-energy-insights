"""Editions: each window of the year plays a seasonal badge against the last."""

from __future__ import annotations

from datetime import date, timedelta

from custom_components.solar_energy_insights.core.achievements.editions import (
    editions,
    in_season,
    label,
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


def test_an_edition_that_beats_the_last_is_crowned_on_the_day_it_passes() -> None:
    played = editions(
        "winter", winters(5, 3, 4), date(2023, 12, 1), date(2026, 3, 1), False
    )

    assert [e.label for e in played] == ["2023/24", "2024/25", "2025/26"]
    assert [e.days for e in played] == [5, 3, 4]
    assert [e.crowned_on for e in played] == [None, None, date(2025, 12, 25)]


def test_a_partial_first_edition_is_no_bar_to_beat() -> None:
    # History begins in the middle of a winter.
    played = editions(
        "winter", winters(0, 1), date(2024, 2, 1), date(2025, 3, 1), False
    )

    assert played[0].partial
    assert played[0].days == 0
    assert played[1].crowned_on is None


def test_the_edition_now_is_the_last_one_even_out_of_season() -> None:
    played = editions(
        "midwinter", [], date(2025, 1, 15), date(2026, 6, 1), southern=False
    )

    # The last window opened in December 2025; the next opens later.
    assert played[-1].start == date(2025, 12, 11)
    assert played[-1].end == date(2025, 12, 31)
