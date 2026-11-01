"""The three answers, from the battery, Solcast and the house's recent days."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from custom_components.solar_energy_insights.core.battery import Battery
from custom_components.solar_energy_insights.core.plan import PlanInputs, plan
from tests.core.helpers import TZ, at, constant, house_use, sunny

DAY = date(2026, 6, 10)
NEXT = DAY + timedelta(days=1)


def evening_heavy(hour: int) -> float:
    return 1.5 if 18 <= hour < 21 else 0.4


def inputs(hour: int, soc: float, **kwargs: Any) -> PlanInputs:
    values: dict[str, Any] = {
        "now": at(DAY, hour),
        "tz": TZ,
        "sunrise": at(NEXT, 5, 15) if hour >= 5 else at(DAY, 5, 15),
        "sunset": at(DAY, 21, 20) if hour < 21 else at(NEXT, 21, 20),
        "battery": Battery(capacity_kwh=15.0, soc_pct=soc, floor_pct=10.0),
        "pv": sunny(DAY, NEXT),
        "use": house_use(14, evening_heavy, DAY),
    }
    values.update(kwargs)
    return PlanInputs(**values)


def test_a_sunny_morning_fills_the_battery_and_lasts() -> None:
    result = plan(inputs(9, 30))

    assert result.charge.full_at is not None
    assert result.charge.full_at < at(DAY, 21)
    assert result.night is not None
    assert result.night.share == 1


def test_clouds_fill_it_later_or_not_at_all() -> None:
    result = plan(inputs(9, 30))

    cloudy = result.charge.full_at_cloudy
    assert cloudy is None or cloudy >= result.charge.full_at  # type: ignore[operator]
    assert result.charge.peak_pct_cloudy <= result.charge.peak_pct


def test_a_low_battery_in_the_evening_needs_the_grid() -> None:
    result = plan(inputs(20, 25))

    assert result.night is not None
    assert result.night.share == 0
    reserve_at = result.night.typical.reserve_at
    assert reserve_at is not None
    assert at(DAY, 20) < reserve_at < at(NEXT, 5, 15)


def test_a_heavy_night_is_no_lighter_than_a_typical_one() -> None:
    varied = {
        **house_use(14, lambda _h: 0.3, DAY),
        **{(DAY - timedelta(days=n), 22): 4.0 for n in (2, 5, 9)},
    }

    result = plan(inputs(21, 50, use=varied))

    assert result.night is not None
    assert result.night.heavy.load_kwh >= result.night.typical.load_kwh
    assert 0 < result.night.lasting <= len(result.night.outcomes)


def test_an_extra_load_leaves_less_for_the_night() -> None:
    alone = plan(inputs(19, 60))
    with_dryer = plan(inputs(19, 60, extra=constant(1.0)))

    assert alone.night is not None
    assert with_dryer.night is not None
    assert with_dryer.night.typical.spare_kwh < alone.night.typical.spare_kwh


def test_without_history_there_is_no_night_answer_yet() -> None:
    result = plan(inputs(20, 50, use={}))

    assert result.night is None


def test_the_best_window_is_where_the_sun_beats_the_house() -> None:
    window = plan(inputs(7, 50)).best_window

    assert window is not None
    assert at(DAY, 7) <= window.start < window.end <= at(DAY, 21, 20)
    assert window.kwh > 0


def test_a_full_battery_exporting_is_free_power() -> None:
    assert plan(inputs(13, 100, export_kw=3.0)).free_power
    assert not plan(inputs(13, 60, export_kw=3.0)).free_power


def test_charging_to_what_is_needed_gets_through_the_heavy_night() -> None:
    result = plan(inputs(21, 40))
    assert result.needed is not None
    needed = result.needed.heavy_pct
    assert needed is not None

    enough = plan(inputs(21, needed))
    short = plan(inputs(21, needed - 3))

    assert enough.night is not None
    assert short.night is not None
    assert enough.night.heavy.lasts
    assert not short.night.heavy.lasts


def test_a_night_no_battery_could_cover_needs_more_than_full() -> None:
    hungry = house_use(14, lambda _h: 3.0, DAY)

    result = plan(inputs(21, 50, use=hungry))

    assert result.needed is not None
    assert result.needed.heavy_pct is None


def test_more_sun_leaves_more_surplus() -> None:
    dull = plan(inputs(9, 60, pv=sunny(DAY, NEXT, peak_kw=4)))
    bright = plan(inputs(9, 60, pv=sunny(DAY, NEXT, peak_kw=10)))

    assert bright.surplus.total_kwh > dull.surplus.total_kwh
    assert bright.surplus.cloudy_kwh <= bright.surplus.total_kwh


def test_free_power_says_until_when() -> None:
    result = plan(inputs(12, 100, export_kw=3.0))

    assert result.free_power
    assert result.free_until is not None
    assert at(DAY, 12) < result.free_until <= at(DAY, 21, 20)


def test_when_even_a_full_battery_is_short_it_says_by_how_much() -> None:
    hungry = house_use(14, lambda _h: 3.0, DAY)

    result = plan(inputs(21, 50, use=hungry))

    assert result.needed is not None
    assert result.needed.heavy_pct is None
    assert result.needed.heavy_short_kwh > 0
    assert result.needed.heavy_short_kwh >= result.needed.typical_short_kwh


def test_when_a_full_battery_is_enough_nothing_is_short() -> None:
    result = plan(inputs(21, 40))

    assert result.needed is not None
    assert result.needed.heavy_pct is not None
    assert result.needed.heavy_short_kwh == 0


def test_the_nights_budget_is_what_the_battery_holds_above_its_reserve() -> None:
    from tests.core.scenarios import SEASONS, heavy_evenings, history, inputs

    winter = SEASONS[1]
    now = at(winter.day, 20)
    i = inputs(winter, now, peak_kw=0, use=history(winter.day, heavy_evenings), soc=80)

    night = plan(i).night

    assert night is not None
    assert night.budget_kwh is not None
    b = i.battery
    assert night.budget_kwh == pytest.approx(
        (80 - b.floor_pct) / 100 * b.capacity_kwh * b.eta, abs=0.01
    )
    late = sorted(o.late_kwh for o in night.outcomes)
    assert night.evening_budget_kwh == pytest.approx(
        night.budget_kwh - late[len(late) // 2], abs=0.15
    )
