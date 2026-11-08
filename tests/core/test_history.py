"""The typical week, the sun against the forecast, and the battery's size."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.solar_energy_insights.core.achievements.facts import DayFacts
from custom_components.solar_energy_insights.core.history import (
    DayVerdict,
    Sizing,
    day_ratios,
    mean_ratio,
    sized_days,
    sizing,
    typical_week,
)

TODAY = date(2026, 6, 10)  # a Wednesday


def use_for(days: int, kwh_at) -> dict[tuple[date, int], float]:
    return {
        (TODAY - timedelta(days=n), h): kwh_at(TODAY - timedelta(days=n), h)
        for n in range(1, days + 1)
        for h in range(24)
    }


def test_the_week_starts_on_monday_and_keeps_the_weekend_apart() -> None:
    week = typical_week(
        use_for(35, lambda d, h: 1.0 if d.weekday() >= 5 and h == 12 else 0.2),
        TODAY,
    )

    assert len(week) == 168
    assert week[0] == pytest.approx(0.2)
    assert week[5 * 24 + 12] == pytest.approx(1.0)  # Saturday noon
    assert week[6 * 24 + 12] == pytest.approx(1.0)  # Sunday noon
    assert week[2 * 24 + 12] == pytest.approx(0.2)  # Wednesday noon


def test_today_and_older_days_are_left_out() -> None:
    use = use_for(60, lambda d, _h: 5.0 if d < TODAY - timedelta(days=35) else 0.5)
    use[(TODAY, 9)] = 9.0

    week = typical_week(use, TODAY)

    assert max(week) == pytest.approx(0.5)


def test_an_hour_never_seen_is_the_mean_of_the_rest() -> None:
    use = {k: v for k, v in use_for(35, lambda _d, _h: 0.4).items() if k[1] != 3}

    week = typical_week(use, TODAY)

    assert week[3] == pytest.approx(0.4)


def facts(day: date, **kw) -> DayFacts:
    return DayFacts(day=day, house_kwh=10.0, **kw)


def test_the_ratio_weighs_bright_days_more_and_skips_tiny_forecasts() -> None:
    days = [
        facts(TODAY - timedelta(days=3), forecast_kwh=40.0, solar_kwh=36.0),
        facts(TODAY - timedelta(days=2), forecast_kwh=4.0, solar_kwh=8.0),
        facts(TODAY - timedelta(days=1), forecast_kwh=0.5, solar_kwh=3.0),
        facts(TODAY - timedelta(days=4), solar_kwh=20.0),
    ]

    ratios = day_ratios(days)

    assert [r.day for r in ratios] == [
        TODAY - timedelta(days=3),
        TODAY - timedelta(days=2),
    ]
    assert ratios[1].ratio == pytest.approx(2.0)
    assert mean_ratio(ratios) == pytest.approx(44 / 44)
    assert mean_ratio([]) is None


FULL = datetime(2026, 6, 1, 11, tzinfo=UTC)


def sized(lasted: int, ran_out: int, never: int) -> list[DayFacts]:
    out, day = [], TODAY - timedelta(days=lasted + ran_out + never + 1)
    for kind in ["lasted"] * lasted + ["ran out"] * ran_out + ["never"] * never:
        day += timedelta(days=1)
        if kind == "never":
            out.append(facts(day, soc_max=70.0))
        else:
            out.append(
                facts(day, soc_max=100.0, full_at=FULL, night_lasted=kind == "lasted")
            )
    return out


def test_each_day_is_full_and_lasted_ran_out_or_never_full() -> None:
    days = sized_days(sized(1, 1, 1))

    assert [d.verdict for d in days] == [
        DayVerdict.RIGHT,
        DayVerdict.TOO_SMALL,
        DayVerdict.NEVER_FULL,
    ]


def test_a_night_not_yet_known_has_no_verdict() -> None:
    open_night = facts(TODAY - timedelta(days=1), soc_max=100.0, full_at=FULL)

    assert sized_days([open_night]) == []


@pytest.mark.parametrize(
    ("lasted", "ran_out", "verdict"),
    (
        (10, 0, Sizing.RIGHT_SIZE),
        (8, 2, Sizing.SOMETIMES_TOO_SMALL),
        (5, 5, Sizing.TOO_SMALL),
        (2, 2, Sizing.NOT_ENOUGH_DAYS),
    ),
)
def test_the_verdict_is_the_share_of_full_days_that_ran_out(
    lasted, ran_out, verdict
) -> None:
    result, share = sizing(sized_days(sized(lasted, ran_out, 3)))

    assert result is verdict
    if verdict is not Sizing.NOT_ENOUGH_DAYS:
        assert share == pytest.approx(ran_out / (lasted + ran_out))
