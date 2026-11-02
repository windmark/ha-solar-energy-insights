"""Off the grid: the inverter's own draw allowed, and stretches across days."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.solar_energy_insights.core.achievements.facts import (
    MAX_DRAW_KWH,
    OFF_GRID_KWH,
    DayFacts,
    _offgrid_runs,
    background_draw,
    with_stretches,
)

DAY = date(2026, 6, 14)


def hours(day: date, kwh: list[float]) -> dict[tuple[date, int], float]:
    return {(day, h): v for h, v in enumerate(kwh)}


def test_runs_from_midnight_to_midnight_and_the_longest() -> None:
    # Grid from 06 to 08 and at 20; the rest off the grid.
    kwh = [0.0] * 24
    for h in (6, 7, 20):
        kwh[h] = 0.4
    runs = _offgrid_runs(hours(DAY, kwh), DAY, OFF_GRID_KWH, known=True)

    assert runs == {"offgrid_head": 6.0, "offgrid_tail": 3.0, "offgrid_run": 12.0}


def test_a_missing_hour_breaks_a_run() -> None:
    imported = hours(DAY, [0.0] * 24)
    del imported[(DAY, 12)]

    assert _offgrid_runs(imported, DAY, OFF_GRID_KWH, known=True)["offgrid_run"] == 12.0


def plenty(days: int, draw: list[float]) -> tuple[dict, dict]:
    """Hours with the battery at 60 %, importing the given draws in turn."""
    imported, soc = {}, {}
    for d in range(days):
        day = DAY - timedelta(days=d)
        for h in range(24):
            imported[(day, h)] = draw[(d * 24 + h) % len(draw)]
            soc[(day, h)] = 60.0
    return imported, soc


def test_the_inverters_own_draw_is_learned_from_hours_the_battery_had_plenty() -> None:
    imported, soc = plenty(5, [0.01, 0.03, 0.04, 0.02])
    # Real grid use, with the battery at its reserve: not the inverter's draw.
    for h in range(10):
        imported[(DAY + timedelta(days=1), h)] = 0.9
        soc[(DAY + timedelta(days=1), h)] = 6.0

    draw, seen = background_draw(imported, soc, floor_pct=5.0)

    assert seen == 120
    assert draw == pytest.approx(0.0401)  # just above the draw
    # Statistics carry rounding noise: an hour a hair above the draw still counts.
    assert draw > 0.04000000001


def test_the_draw_is_capped_so_real_grid_use_is_never_excused() -> None:
    imported, soc = plenty(5, [0.3])

    assert background_draw(imported, soc, floor_pct=5.0)[0] == MAX_DRAW_KWH


def test_until_there_are_enough_hours_the_plain_threshold_holds() -> None:
    imported, soc = plenty(1, [0.05])

    assert background_draw(imported, soc, floor_pct=5.0) == (OFF_GRID_KWH, 24)


def day(back: int, head: float, tail: float, run: float) -> DayFacts:
    return DayFacts(
        day=DAY - timedelta(days=back),
        house_kwh=10.0,
        offgrid_head=head,
        offgrid_tail=tail,
        offgrid_run=run,
    )


def test_a_stretch_carries_across_midnight_and_whole_days() -> None:
    days = [
        day(3, 0, 6, 6),  # off the grid from 18:00
        day(2, 24, 24, 24),  # all day
        day(1, 7, 2, 7),  # until 07:00
        day(0, 3, 0, 9),
    ]

    stretches = [f.offgrid_stretch for f in with_stretches(days)]

    assert stretches == [6.0, 30.0, 37.0, 9.0]


def test_a_missing_day_breaks_a_stretch() -> None:
    days = [day(2, 0, 6, 6), day(0, 5, 0, 5)]

    assert [f.offgrid_stretch for f in with_stretches(days)] == [6.0, 5.0]


def test_the_inverters_draw_is_not_counted_as_grid_use() -> None:
    f = DayFacts(day=DAY, house_kwh=12.0, import_kwh=0.9, background_kwh=0.96)
    g = DayFacts(day=DAY, house_kwh=12.0, import_kwh=0.9)

    assert f.grid_free is True
    assert f.self_sufficiency == 1.0
    assert g.grid_free is False


def test_beyond_the_allowance_it_is_grid_use() -> None:
    f = DayFacts(day=DAY, house_kwh=12.0, import_kwh=3.0, background_kwh=0.96)

    assert f.own_import_kwh == pytest.approx(2.04)
    assert f.grid_free is False
