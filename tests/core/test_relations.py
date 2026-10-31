"""How plans relate to each other: more charge never hurts, more load never
helps, nothing jumps at sunset or midnight, and odd input is handled."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.solar_energy_insights.core.plan import Night, plan
from tests.core.helpers import TZ, at
from tests.core.scenarios import (
    HOUSES,
    SEASONS,
    SKIES,
    erratic,
    heavy_evenings,
    history,
    inputs,
)

SUMMER, WINTER = SEASONS


def night_of(*args, **kwargs) -> Night:
    result = plan(inputs(*args, **kwargs)).night
    assert result is not None
    return result


@pytest.mark.parametrize(
    ("season", "house"), itertools.product(SEASONS, HOUSES.values()), ids=str
)
def test_more_charge_never_makes_the_night_worse(season, house) -> None:
    use = history(season.day, house)
    for hour in (2, 10, 20):
        now = at(season.day, hour)
        nights = [
            night_of(season, now, peak_kw=SKIES["dull"], use=use, soc=soc)
            for soc in (10, 30, 60, 90)
        ]
        shares = [n.share for n in nights]
        spares = [n.typical.spare_kwh for n in nights]
        assert shares == sorted(shares), hour
        assert spares == sorted(spares), hour


@pytest.mark.parametrize("season", SEASONS, ids=lambda s: s.name)
def test_more_load_never_makes_the_night_better(season) -> None:
    light = history(season.day, heavy_evenings)
    heavy = {key: kwh * 1.5 for key, kwh in light.items()}
    now = at(season.day, 20)

    a = plan(inputs(season, now, peak_kw=0, use=light, soc=50))
    b = plan(inputs(season, now, peak_kw=0, use=heavy, soc=50))

    assert a.night is not None
    assert b.night is not None
    assert b.night.share <= a.night.share
    assert b.night.typical.spare_kwh <= a.night.typical.spare_kwh
    assert a.needed is not None
    assert b.needed is not None
    assert (b.needed.heavy_pct or 101) >= (a.needed.heavy_pct or 101)


def test_charge_needed_does_not_depend_on_the_charge_now() -> None:
    use = history(WINTER.day, erratic)
    now = at(WINTER.day, 22)

    needed = {
        soc: plan(inputs(WINTER, now, peak_kw=0, use=use, soc=soc)).needed
        for soc in (10, 50, 90)
    }

    assert len({(n.typical_pct, n.heavy_pct) for n in needed.values() if n}) == 1


@pytest.mark.parametrize("house", HOUSES.values(), ids=lambda h: h.__name__)
def test_charge_needed_and_lasting_agree_at_night(house) -> None:
    use = history(WINTER.day, house)
    now = at(WINTER.day, 21)
    for soc in range(10, 101, 10):
        p = plan(inputs(WINTER, now, peak_kw=0, use=use, soc=soc))
        assert p.needed is not None
        assert p.night is not None
        heavy = p.needed.heavy_pct
        typical = p.needed.typical_pct
        if heavy is not None and soc >= heavy:
            assert p.night.heavy.lasts, soc
        if typical is not None and soc < typical - 1:
            assert not p.night.typical.lasts, soc


def test_a_sunny_day_that_fills_the_battery_leaves_the_same_night() -> None:
    """Once the battery will be full at sunset, how empty it is this morning
    changes nothing about tonight."""
    use = history(SUMMER.day, heavy_evenings)
    now = at(SUMMER.day, 8)

    nights = [
        night_of(SUMMER, now, peak_kw=SKIES["sunny"], use=use, soc=soc)
        for soc in (10, 40, 80)
    ]

    assert len({(n.share, round(n.typical.spare_kwh, 6)) for n in nights}) == 1


@pytest.mark.parametrize(
    ("season", "moment"),
    [(s, "sunset") for s in SEASONS] + [(s, "midnight") for s in SEASONS],
    ids=lambda x: getattr(x, "name", x),
)
def test_nothing_jumps_at_sunset_or_midnight(season, moment) -> None:
    use = history(season.day, erratic)
    middle = (
        at(season.day, *season.set)
        if moment == "sunset"
        else at(season.day + timedelta(days=1), 0)
    )
    before, after = middle - timedelta(minutes=2), middle + timedelta(minutes=2)

    a = night_of(season, before, peak_kw=SKIES["dull"], use=use, soc=60)
    b = night_of(season, after, peak_kw=SKIES["dull"], use=use, soc=60)

    assert a.share == b.share
    assert a.typical.spare_kwh == pytest.approx(b.typical.spare_kwh, abs=0.15)
    assert a.typical.load_kwh == pytest.approx(b.typical.load_kwh, abs=0.15)


def test_an_extra_load_never_makes_the_night_better() -> None:
    use = history(WINTER.day, heavy_evenings)
    now = at(WINTER.day, 18)

    alone = night_of(WINTER, now, peak_kw=0, use=use, soc=70)
    dryer = night_of(WINTER, now, peak_kw=0, use=use, soc=70, extra=lambda _t: 1.0)

    assert dryer.share <= alone.share
    assert dryer.typical.spare_kwh <= alone.typical.spare_kwh


def test_gaps_in_the_history_still_give_an_answer() -> None:
    use = history(WINTER.day, heavy_evenings)
    # A recorder outage: a whole day and a few scattered hours missing.
    gap_day = WINTER.day - timedelta(days=3)
    patchy = {
        key: kwh
        for key, kwh in use.items()
        if key[0] != gap_day and not (key[1] == 22 and key[0].day % 2)
    }

    full = night_of(WINTER, at(WINTER.day, 21), peak_kw=0, use=use, soc=60)
    holes = night_of(WINTER, at(WINTER.day, 21), peak_kw=0, use=patchy, soc=60)

    # A missing hour takes the usual value, not zero: the answer barely moves.
    assert holes.typical.load_kwh == pytest.approx(full.typical.load_kwh, rel=0.1)


def test_fewer_days_than_asked_for_uses_what_there_is() -> None:
    use = history(WINTER.day, heavy_evenings, count=3)

    night = night_of(WINTER, at(WINTER.day, 21), peak_kw=0, use=use, soc=60, nights=14)

    assert len(night.outcomes) == 3


def test_a_battery_below_its_floor_needs_the_grid_straight_away() -> None:
    use = history(WINTER.day, heavy_evenings)
    now = at(WINTER.day, 22)

    night = night_of(WINTER, now, peak_kw=0, use=use, soc=3)

    assert night.share == 0
    assert night.typical.reserve_at == now


def test_weights_that_say_nothing_count_as_equal() -> None:
    use = history(WINTER.day, erratic)
    now = at(WINTER.day, 21)

    equal = night_of(WINTER, now, peak_kw=0, use=use, soc=50)
    zeros = night_of(WINTER, now, peak_kw=0, use=use, soc=50, weights=[0.0] * 7)

    assert zeros.share == equal.share


@pytest.mark.parametrize(
    "night_start_utc",
    (
        # The night the clocks go back (25 Oct) and forward (29 Mar) in Berlin.
        datetime(2026, 10, 24, 20, tzinfo=UTC),
        datetime(2026, 3, 28, 20, tzinfo=UTC),
    ),
    ids=["clocks back", "clocks forward"],
)
def test_clock_changes_keep_the_night_whole(night_start_utc: datetime) -> None:
    day = night_start_utc.astimezone(TZ).date()
    season = type(WINTER)("dst", day, (7, 0), (18, 0))
    use = history(day + timedelta(days=1), heavy_evenings, count=14)

    p = plan(inputs(season, night_start_utc, peak_kw=0, use=use, soc=80))

    assert p.night is not None
    hours = (p.sunrise - p.now).total_seconds() / 3600
    for o in p.night.outcomes:
        # The house used power through every real hour of the night.
        assert o.load_kwh > 0.3 * hours
        assert o.load_kwh < 1.5 * hours
    assert p.needed is not None


def test_a_forecast_with_no_sun_fills_nothing() -> None:
    use = history(SUMMER.day, heavy_evenings)

    p = plan(inputs(SUMMER, at(SUMMER.day, 9), peak_kw=0, use=use, soc=40))

    assert p.charge.full_at is None
    assert p.surplus.total_kwh == 0
    assert p.best_window is None


def test_an_already_full_battery_is_full_now() -> None:
    use = history(SUMMER.day, heavy_evenings)
    now = at(SUMMER.day, 12)

    p = plan(inputs(SUMMER, now, peak_kw=SKIES["sunny"], use=use, soc=100))

    assert p.charge.full_at == now


def test_today_is_never_replayed_as_a_past_day() -> None:
    """Changing what happens later today, or tonight, must not change the answer
    (as if the plan peeked at it)."""
    use = history(WINTER.day, erratic)
    future = {
        **use,
        **{(WINTER.day, h): 9.0 for h in range(24)},
        **{(WINTER.day + timedelta(days=1), h): 9.0 for h in range(24)},
    }
    now = at(WINTER.day, 21)

    a = night_of(WINTER, now, peak_kw=0, use=use, soc=50)
    b = night_of(WINTER, now, peak_kw=0, use=future, soc=50)

    assert (a.share, a.typical.spare_kwh) == (b.share, b.typical.spare_kwh)


def test_one_day_of_history_is_enough_for_an_answer() -> None:
    use = {(WINTER.day - timedelta(days=1), h): 0.5 for h in range(24)}

    p = plan(inputs(WINTER, at(WINTER.day, 21), peak_kw=0, use=use, soc=50))

    assert p.night is not None
    assert len(p.night.outcomes) == 1
    assert p.night.typical == p.night.heavy
