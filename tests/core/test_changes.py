"""What changed in the last half hour, since when, and why."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from custom_components.solar_energy_insights.core.changes import Answers, detect, keep
from tests.core.helpers import at

DAY = date(2026, 11, 1)
T0 = at(DAY, 19, 30)


def answers(minutes: int, **kw) -> Answers:
    base = Answers(
        at=T0 + timedelta(minutes=minutes),
        outlook="lasts",
        grid_from=None,
        spare_kwh=0.8,
        full_at=None,
        house_kwh=1000.0 + 0.02 * minutes,  # 1.2 kW, the usual
        forecast_kwh=0.0,
    )
    return replace(base, **kw)


def series(*points: Answers) -> list[Answers]:
    kept: list[Answers] = []
    for p in points:
        kept = keep(kept, p)
    return kept


def test_ordinary_drift_is_no_change() -> None:
    s = series(answers(0), answers(10, spare_kwh=0.7), answers(20, spare_kwh=0.65))

    assert detect(s, usual_kw=1.2) == {}


def test_an_oven_turning_the_night_is_shown_with_when_and_why() -> None:
    # The oven goes on at 19:52: 2.4 kW above the usual for a while.
    s = series(
        answers(0),
        answers(10),
        answers(
            22,
            house_kwh=1000.44 + 0.3,
            outlook="grid",
            grid_from=at(DAY, 3, 10),
            spare_kwh=0.0,
        ),
        answers(
            30,
            house_kwh=1000.6 + 1.0,
            outlook="grid",
            grid_from=at(DAY, 3, 10),
            spare_kwh=0.0,
        ),
    )

    change = detect(s, usual_kw=1.2)["tonight"]

    assert change.was.outlook == "lasts"
    assert change.since == T0 + timedelta(minutes=22)
    assert change.cause == "house used 1.0 kWh more than usual"


def test_the_change_clears_itself_after_half_an_hour() -> None:
    points = [
        answers(0),
        answers(22, outlook="grid", grid_from=at(DAY, 3, 10), spare_kwh=0.0),
        answers(40, outlook="grid", grid_from=at(DAY, 3, 10), spare_kwh=0.0),
        answers(55, outlook="grid", grid_from=at(DAY, 3, 10), spare_kwh=0.0),
    ]

    # At 20:10 the change from 19:52 is 18 minutes old and shown; at 20:25 it
    # is 33 minutes old and gone.
    assert "tonight" in detect(series(*points[:3]))
    assert detect(series(*points)) == {}


def test_a_grid_time_moving_by_a_quarter_of_an_hour_counts() -> None:
    first = answers(0, outlook="grid", grid_from=at(DAY, 4, 0), spare_kwh=0.0)

    small = detect(
        series(
            first,
            replace(first, at=T0 + timedelta(minutes=10), grid_from=at(DAY, 3, 50)),
        )
    )
    big = detect(
        series(
            first,
            replace(first, at=T0 + timedelta(minutes=10), grid_from=at(DAY, 3, 40)),
        )
    )

    assert small == {}
    assert "tonight" in big


def test_a_full_time_moved_by_the_forecast_names_the_forecast() -> None:
    noon = replace(answers(0), full_at=at(DAY, 13, 22), forecast_kwh=12.0)
    later = replace(
        noon,
        at=T0 + timedelta(minutes=20),
        full_at=at(DAY, 14, 40),
        forecast_kwh=8.0,
        house_kwh=(noon.house_kwh or 0) + 0.4,
    )

    change = detect(series(noon, later), usual_kw=1.2)["full_at"]

    assert change.was.full_at == at(DAY, 13, 22)
    assert change.cause == "Solcast now expects 4.0 kWh less sun"


def test_keeping_holds_only_the_half_hour_and_the_moment_before_it() -> None:
    s = series(*(answers(m) for m in range(0, 100, 5)))

    assert s[0].at <= s[-1].at - timedelta(minutes=30) < s[1].at


def test_a_longer_look_back_keeps_a_change_shown_longer() -> None:
    hour = timedelta(hours=1)
    points = [
        answers(0),
        answers(22, outlook="grid", grid_from=at(DAY, 3, 10), spare_kwh=0.0),
        answers(55, outlook="grid", grid_from=at(DAY, 3, 10), spare_kwh=0.0),
    ]
    kept: list[Answers] = []
    for p in points:
        kept = keep(kept, p, hour)

    # 33 minutes after it: gone with half an hour, still shown with an hour.
    assert detect(series(*points)) == {}
    assert detect(kept, window=hour)["tonight"].since == T0 + timedelta(minutes=22)
