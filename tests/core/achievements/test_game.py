"""The score, levels, ranks, streaks and records."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from custom_components.solar_energy_insights.core.achievements.game import (
    level_of,
    rank_of,
    records,
    score,
    score_parts,
    streaks,
    xp_for_level,
)
from tests.core.achievements.homes import home, without
from tests.core.helpers import TZ

DAY = date(2026, 10, 31)


def test_a_perfect_day_scores_100() -> None:
    f = replace(
        home(1, DAY)[0],
        import_kwh=0.0,
        export_kwh=0.0,
        soc_max=100.0,
        soc_min=5.0,
        solar_kwh=20.0,
        forecast_kwh=18.0,
    )

    assert score(f) == 100


def test_what_a_home_cannot_measure_drops_out_and_the_rest_counts_fully() -> None:
    f = replace(home(1, DAY)[0], import_kwh=0.0, soc_max=100.0, soc_min=5.0)
    bare = without("solar_kwh", "export_kwh", "forecast_kwh")(f)

    assert set(score_parts(bare)) == {"self_sufficiency", "battery_used"}
    assert score(bare) == 100


def test_levels_keep_the_old_curve_and_gain_names() -> None:
    assert xp_for_level(1) == 100
    assert level_of(0).level == 1
    assert level_of(99).level == 1
    assert level_of(100).level == 2
    assert level_of(0).name == "Spark"
    assert level_of(xp_for_level(9)).name == "Photon"
    assert level_of(xp_for_level(30)).name.startswith("Zenith ")


def test_a_dark_season_brings_the_rank_cut_offs_down() -> None:
    from custom_components.solar_energy_insights.core.achievements.profile import (
        Season,
        season_of,
    )

    today = date(2027, 2, 15)
    days = [today - timedelta(days=n) for n in range(400, -1, -1)]
    # Winters score 50, the rest of the year 80: a dark winter is normal here.
    scores = [(d, 50 if season_of(d) is Season.WINTER else 80) for d in days]

    rank = rank_of(scores, today, southern=False)
    unadjusted = rank_of(scores[-30:], today, southern=False)

    assert rank.season_factor < 1
    assert unadjusted.name == "Silver"
    assert rank.name in ("Gold", "Platinum")


def test_streaks_count_and_remember_the_best() -> None:
    days = home(20, DAY)
    flags = [True] * 5 + [False] + [True] * 3
    days = days[:9]
    days = [
        replace(f, night_lasted=flag, forecast_kwh=20.0)
        for f, flag in zip(days, flags, strict=True)
    ]

    night = next(s for s in streaks(days, southern=False) if s.id == "grid_free_night")

    assert night.current == 3
    assert night.best == 5


def test_a_shield_absorbs_a_break_the_weather_caused() -> None:
    base = [replace(f, night_lasted=True, forecast_kwh=20.0) for f in home(30, DAY)]
    # Day 29 breaks the run on one of the season's gloomiest forecasts.
    stormy = [
        *base[:-2],
        replace(base[-2], night_lasted=False, forecast_kwh=1.0),
        base[-1],
    ]
    lazy = [
        *base[:-2],
        replace(base[-2], night_lasted=False, forecast_kwh=20.0),
        base[-1],
    ]

    shielded = next(s for s in streaks(stormy, False) if s.id == "grid_free_night")
    broken = next(s for s in streaks(lazy, False) if s.id == "grid_free_night")

    assert shielded.current == 29
    assert shielded.shields < 2
    assert broken.current == 1


def test_unknown_days_do_not_break_a_streak() -> None:
    days = [replace(f, night_lasted=True) for f in home(6, DAY)]
    days[3] = replace(days[3], night_lasted=None)

    night = next(s for s in streaks(days, False) if s.id == "grid_free_night")

    assert night.current == 5


def test_records_know_the_best_the_seasons_best_and_where_the_last_day_ranks() -> None:
    days = home(120, DAY)
    best = max(days, key=lambda f: f.solar_kwh or 0)

    recs = {r.id: r for r in records(days, TZ, southern=False)}

    assert recs["best_day"].value == best.solar_kwh
    assert recs["best_day"].day == best.day
    assert recs["best_day"].season_value is not None
    top = recs["best_day"].last_top_pct
    assert top is not None
    assert 0 < top <= 100
    assert recs["earliest_full"].value == min(
        m for f in days if (m := f.full_minutes(TZ)) is not None
    )
    assert recs["thriftiest"].value == pytest.approx(9.0)
