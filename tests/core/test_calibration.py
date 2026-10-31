"""Learning from recent evenings: clean history, honest scores, a hidden load
only when it predicts better, and a weighting that does not flip."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.solar_energy_insights.core.backtest import (
    STANDBY_STEPS_W,
    Evening,
    Weighting,
    calibrate,
    clean_lows,
)
from custom_components.solar_energy_insights.core.battery import Battery, simulate
from tests.core.helpers import TZ, at

DAY = date(2026, 11, 20)
BATTERY = Battery(capacity_kwh=10.0, soc_pct=50.0, floor_pct=5.0)


def use_for(days: int) -> dict[tuple[date, int], float]:
    """A steady house, as real ones mostly are: small differences from day to
    day, the same on every run. A hidden load then shows; in a house whose nights
    vary wildly it would be lost in the noise, and is rightly not guessed."""
    out = {}
    for n in range(days + 1):
        day = DAY - timedelta(days=n)
        level = 0.3 + (n * 37 % 11) / 200
        for hour in range(24):
            evening = 0.9 if 17 <= hour < 22 else 0.0
            out[(day, hour)] = round(level + evening, 3)
    return out


def lived(
    use: dict[tuple[date, int], float], count: int, hidden_kw: float, socs: list[float]
) -> list[Evening]:
    """What the battery really did: each night run with its own load, plus a
    load the house counter never saw."""
    out = []
    for k, n in enumerate(range(count, 0, -1)):
        day = DAY - timedelta(days=n)
        start, sunrise = at(day, 21), at(day + timedelta(days=1), 7)

        def load(t: datetime) -> float:
            local = t.astimezone(TZ)
            return use.get((local.date(), local.hour), 0.0) + hidden_kw

        battery = Battery(capacity_kwh=10.0, soc_pct=socs[k % len(socs)], floor_pct=5.0)
        sim = simulate(battery, start, sunrise, lambda _t: 0.0, load)
        out.append(
            Evening(day, battery.soc_pct, sunrise, min(p for _, p in sim.points))
        )
    return out


SOCS = [45.0, 60.0, 75.0, 90.0, 5.0]


def test_impossible_readings_are_cleaned_and_counted() -> None:
    t = datetime(2026, 10, 28, 20, tzinfo=UTC)
    hours = {
        t: (0.0, 47.0),  # one 0 % reading in an hour that sat at 47 %: a glitch
        t + timedelta(hours=1): (0.0, 2.0),  # asleep at the reserve, read as 0
        t + timedelta(hours=2): (31.0, 33.0),  # a normal hour
        t + timedelta(hours=3): (None, None),  # nothing recorded
    }

    cleaned, corrected = clean_lows(hours, floor_pct=5.0)

    assert corrected == 2
    assert cleaned[t] == 47.0
    assert cleaned[t + timedelta(hours=1)] == 5.0
    assert cleaned[t + timedelta(hours=2)] == 31.0
    assert t + timedelta(hours=3) not in cleaned


def test_a_hidden_load_is_found_and_removes_the_optimism() -> None:
    use = use_for(40)
    evenings = lived(use, 14, hidden_kw=0.06, socs=SOCS)

    cal = calibrate(use, TZ, evenings, BATTERY, 14)

    assert 0.03 <= cal.standby_kw <= 0.09
    assert cal.uncorrected.bias_kwh is not None
    assert cal.uncorrected.bias_kwh > 0.3
    assert cal.score.bias_kwh is not None
    assert abs(cal.score.bias_kwh) < 0.15
    honest = cal.honest_error_kwh[cal.weighting]
    assert honest is not None
    assert cal.uncorrected.error_kwh is not None
    assert honest < cal.uncorrected.error_kwh


def test_no_hidden_load_is_invented_when_there_is_none() -> None:
    use = use_for(40)
    evenings = lived(use, 14, hidden_kw=0.0, socs=SOCS)

    cal = calibrate(use, TZ, evenings, BATTERY, 14)

    assert cal.standby_kw == 0


def test_evenings_that_start_empty_do_not_count() -> None:
    use = use_for(40)
    mixed = lived(use, 14, hidden_kw=0.0, socs=SOCS)
    empty = [e for e in mixed if e.start_soc <= 5.0]
    useful = [e for e in mixed if e.start_soc > 5.0]

    with_empty = calibrate(use, TZ, mixed, BATTERY, 14)
    without = calibrate(use, TZ, useful, BATTERY, 14)

    assert empty
    assert len(with_empty.score.contested) == len(without.score.contested)
    assert with_empty.score.error_kwh == pytest.approx(without.score.error_kwh)
    assert with_empty.score.agreement == pytest.approx(without.score.agreement)


# Afternoons are alike here, so similar afternoons weigh exactly like equal.
@pytest.mark.parametrize("current", (Weighting.EQUAL, Weighting.SIMILAR), ids=str)
def test_the_weighting_in_use_stays_without_a_clear_gain(current: Weighting) -> None:
    # Every afternoon alike: nights-like-tonight cannot tell days apart.
    use = {
        (DAY - timedelta(days=n), hour): (0.4 if hour < 21 else 0.4 + (n % 4) * 0.3)
        for n in range(41)
        for hour in range(24)
    }

    cal = calibrate(use, TZ, lived(use, 14, 0.0, SOCS), BATTERY, 14, current)

    assert cal.weighting is current


def test_too_few_evenings_teach_nothing() -> None:
    use = use_for(40)
    evenings = lived(use, 3, hidden_kw=0.08, socs=[60.0, 75.0, 90.0])

    cal = calibrate(use, TZ, evenings, BATTERY, 14, Weighting.EQUAL)

    assert cal.standby_kw == 0
    assert cal.weighting is Weighting.EQUAL


def test_the_hidden_load_searched_covers_a_typical_inverter() -> None:
    assert min(STANDBY_STEPS_W) == 0
    assert max(STANDBY_STEPS_W) >= 100
