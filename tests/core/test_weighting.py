"""Nights like tonight count more, and only once that proves better."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from custom_components.solar_energy_insights.core.backtest import (
    Evening,
    Weighting,
    calibrate,
    score,
)
from custom_components.solar_energy_insights.core.battery import Battery, simulate
from custom_components.solar_energy_insights.core.nights import (
    past_days,
    similarity_weights,
)
from tests.core.helpers import TZ, at

DAY = date(2026, 11, 20)
BATTERY = Battery(capacity_kwh=15.0, soc_pct=60.0, floor_pct=10.0)


def busy(day: date) -> bool:
    """Busy evenings (every third day) draw more from the afternoon through the
    night that follows, so the afternoon tells what the night will be."""
    return day.toordinal() % 3 == 0


def kwh(day: date, hour: int) -> float:
    if hour >= 15 and busy(day):
        return 1.2
    if hour < 6 and busy(day - timedelta(days=1)):
        return 1.2
    return 0.3


def history(days: int, until: date) -> dict[tuple[date, int], float]:
    return {
        (until - timedelta(days=n), hour): kwh(until - timedelta(days=n), hour)
        for n in range(days + 1)
        for hour in range(24)
    }


def test_days_like_today_weigh_more() -> None:
    today = next(d for d in (DAY - timedelta(days=n) for n in range(5)) if busy(d))
    use = history(14, today)
    days = past_days(use, TZ, 14)

    weights = similarity_weights(use, TZ, at(today, 21), days)

    busy_weight = [
        w
        for d, w in zip(days, weights, strict=True)
        if busy(today - timedelta(days=d.days_ago))
    ]
    calm_weight = [
        w
        for d, w in zip(days, weights, strict=True)
        if not busy(today - timedelta(days=d.days_ago))
    ]
    assert min(busy_weight) > max(calm_weight)


def test_without_anything_to_compare_all_weigh_the_same() -> None:
    # Whole past days, but nothing recorded today to compare them with.
    use = {
        (DAY - timedelta(days=n), h): 0.3 + n / 10
        for n in range(1, 6)
        for h in range(24)
    }
    days = past_days(use, TZ, 5, today=DAY)

    assert len(set(similarity_weights(use, TZ, at(DAY, 21), days))) == 1


def evenings(use: dict[tuple[date, int], float], count: int) -> list[Evening]:
    """What the battery really did: each night replayed with its own load."""
    out = []
    for n in range(count, 0, -1):
        day = DAY - timedelta(days=n)
        start, sunrise = at(day, 21), at(day + timedelta(days=1), 7)

        def own(t: datetime) -> float:
            local = t.astimezone(TZ)
            return use.get((local.date(), local.hour), 0.0)

        sim = simulate(BATTERY, start, sunrise, lambda _t: 0.0, own)
        lowest = min(pct for _, pct in sim.points)
        out.append(Evening(day, BATTERY.soc_pct, sunrise, lowest))
    return out


def test_the_backtest_picks_similarity_when_it_predicts_better() -> None:
    use = history(40, DAY)
    nights = evenings(use, 14)

    cal = calibrate(use, TZ, nights, BATTERY, 14)

    assert cal.weighting is Weighting.SIMILAR
    equal = cal.honest_error_kwh[Weighting.EQUAL]
    similar = cal.honest_error_kwh[Weighting.SIMILAR]
    assert similar is not None
    assert equal is not None
    assert similar < equal


def test_equal_weights_stay_when_the_afternoon_misleads() -> None:
    """Afternoons that point at the wrong nights must not be trusted."""
    use = {
        (DAY - timedelta(days=n), hour): (
            1.2
            if (hour >= 15 and (DAY - timedelta(days=n)).toordinal() % 3 == 0)
            or (hour < 6 and (DAY - timedelta(days=n)).toordinal() % 3 == 0)
            else 0.3
        )
        for n in range(41)
        for hour in range(24)
    }

    cal = calibrate(use, TZ, evenings(use, 14), BATTERY, 14)

    assert cal.weighting is Weighting.EQUAL


def test_equal_weights_stay_when_the_afternoon_says_nothing() -> None:
    use = {
        (DAY - timedelta(days=n), hour): (
            1.5 if n % 3 == 0 and (hour >= 22 or hour < 5) else 0.3
        )
        for n in range(41)
        for hour in range(24)
    }

    cal = calibrate(use, TZ, evenings(use, 14), BATTERY, 14)

    assert cal.weighting is Weighting.EQUAL


def test_scores_only_use_days_before_each_evening() -> None:
    use = history(40, DAY)
    nights = evenings(use, 10)
    # Change what happened after the scored evenings; the scores must not move.
    later = {key: (99.0 if key[0] >= DAY else value) for key, value in use.items()}

    first = score(use, TZ, nights, BATTERY, 14, Weighting.SIMILAR)
    second = score(later, TZ, nights, BATTERY, 14, Weighting.SIMILAR)

    assert [n.predicted_spare_kwh for n in first.nights] == [
        n.predicted_spare_kwh for n in second.nights
    ]
