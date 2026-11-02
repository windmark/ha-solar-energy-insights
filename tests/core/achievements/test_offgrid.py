"""Off the grid: load spikes allowed, and stretches across days."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.solar_energy_insights.core.achievements.facts import (
    MAX_SPIKE_SHARE,
    DayFacts,
    Series,
    _offgrid,
    spike_share,
    with_stretches,
)

DAY = date(2026, 6, 14)
FLOOR = 5.0


def series(imp: list[float], use: list[float], soc: list[float]) -> Series:
    return Series(
        house={(DAY, h): use[h] for h in range(24)},
        imported={(DAY, h): imp[h] for h in range(24)},
        soc_min={(DAY, h): soc[h] for h in range(24)},
    )


def test_a_sliver_of_grid_while_the_battery_carries_the_house_is_off_the_grid() -> None:
    # 1 kWh an hour of use; 3 % of it from the grid as loads switch on.
    s = series([0.03] * 24, [1.0] * 24, [60.0] * 24)

    off = _offgrid(s, DAY, FLOOR, share=0.04, known=True)

    assert off["offgrid_hours"] == 24.0
    assert off["spike_kwh"] == pytest.approx(0.72)


def test_the_same_sliver_with_the_battery_at_its_reserve_is_grid_use() -> None:
    soc = [60.0] * 24
    for h in range(2, 6):
        soc[h] = FLOOR  # at its reserve: the grid is carrying the house
    s = series([0.03] * 24, [1.0] * 24, soc)

    off = _offgrid(s, DAY, FLOOR, share=0.04, known=True)

    assert off["offgrid_hours"] == 20.0
    assert (off["offgrid_head"], off["offgrid_tail"], off["offgrid_run"]) == (
        2.0,
        18.0,
        18.0,
    )


def test_a_large_share_is_grid_use_even_with_charge_left() -> None:
    imp = [0.03] * 24
    imp[19] = 1.5  # oven and kettle beyond what the battery can deliver
    s = series(imp, [1.0] * 23 + [1.0], [60.0] * 24)

    off = _offgrid(s, DAY, FLOOR, share=0.04, known=True)

    assert off["offgrid_hours"] == 23.0


def test_a_missing_hour_breaks_a_run() -> None:
    s = series([0.0] * 24, [1.0] * 24, [60.0] * 24)
    del s.imported[(DAY, 12)]  # type: ignore[union-attr]

    assert _offgrid(s, DAY, FLOOR, share=0.0, known=True)["offgrid_run"] == 12.0


def history(
    days: int, share: list[float], soc: float = 60.0
) -> tuple[dict, dict, dict]:
    imported, house, low = {}, {}, {}
    for d in range(days):
        for h in range(24):
            key = (DAY - timedelta(days=d), h)
            house[key] = 1.0 + (h % 5) * 0.4
            imported[key] = house[key] * share[(d * 24 + h) % len(share)]
            low[key] = soc
    return imported, house, low


def test_the_spike_share_is_learned_from_hours_the_battery_had_plenty() -> None:
    imported, house, low = history(5, [0.0, 0.01, 0.02, 0.03])
    # Real grid use with the battery at its reserve: not a spike.
    for h in range(10):
        key = (DAY + timedelta(days=1), h)
        imported[key], house[key], low[key] = 0.9, 1.0, FLOOR

    share, seen = spike_share(imported, house, low, FLOOR)

    assert seen == 120
    assert share == pytest.approx(0.031)  # just above the high end


def test_the_share_is_capped_so_sustained_grid_use_never_passes() -> None:
    imported, house, low = history(5, [0.5])

    assert spike_share(imported, house, low, FLOOR)[0] == MAX_SPIKE_SHARE


def test_until_there_are_enough_hours_nothing_is_excused() -> None:
    imported, house, low = history(1, [0.03])

    assert spike_share(imported, house, low, FLOOR) == (0.0, 24)


def day(back: int, head: float, tail: float, run: float) -> DayFacts:
    return DayFacts(
        day=DAY - timedelta(days=back),
        house_kwh=10.0,
        offgrid_head=head,
        offgrid_tail=tail,
        offgrid_run=run,
    )


def test_a_stretch_carries_across_midnight_and_whole_days() -> None:
    days = [day(3, 0, 6, 6), day(2, 24, 24, 24), day(1, 7, 2, 7), day(0, 3, 0, 9)]

    assert [f.offgrid_stretch for f in with_stretches(days)] == [6.0, 30.0, 37.0, 9.0]


def test_a_missing_day_breaks_a_stretch() -> None:
    assert [
        f.offgrid_stretch for f in with_stretches([day(2, 0, 6, 6), day(0, 5, 0, 5)])
    ] == [6.0, 5.0]


def test_only_import_excused_as_spikes_is_left_out() -> None:
    f = DayFacts(day=DAY, house_kwh=12.0, import_kwh=0.4, spike_kwh=0.38)
    g = DayFacts(day=DAY, house_kwh=12.0, import_kwh=3.0, spike_kwh=0.38)

    assert f.grid_free is True
    assert g.own_import_kwh == pytest.approx(2.62)
    assert g.grid_free is False
