"""What the dashboard says: real moments word for word, and every moment
within its limits and without contradicting itself."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from custom_components.solar_energy_insights.core.summary import (
    CLAUSE_MAX,
    HEADLINE_MAX,
    Facts,
    Row,
    summarize,
)
from tests.core.helpers import TZ

DAY = date(2026, 11, 2)


def at(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def facts(hour: int, minute: int = 0, **kw: Any) -> Facts:
    now = at(hour, minute) if hour >= 3 else at(hour, minute, DAY + timedelta(days=1))
    base = Facts(
        now=now,
        tz=TZ,
        day=8 <= hour < 17,
        soc=60.0,
        outlook="lasts",
        replayed=14,
        lasting=12,
        recent_nights=14,
        recent_lasted=10,
        evening_budget=6.1,
        usual_evening=4.2,
        peak=100,
        peak_cloudy=88,
        full_at=None,
        sunset_pct=90,
        spare=1.4,
        grid_kwh=0.0,
    )
    return replace(base, **kw)


def row(s: Any, label: str) -> Row:
    return next(r for r in s.rows if r.label == label)


# ── Real moments ──


def test_monday_morning_after_the_guests() -> None:
    # 2 Nov, 09:16: 7 of 14 nights short from 92 % at sunset.
    s = summarize(
        facts(
            9,
            16,
            outlook="borderline",
            lasting=7,
            evening_budget=5.68,
            usual_evening=4.3,
            short_evenings=(3.8, 7.8, 6.0, 3.8, 6.4, 7.9, 6.4),
            full_at=at(13, 26),
        )
    )

    tonight = row(s, "Tonight")
    assert tonight.main == "Borderline tonight"
    assert tonight.detail == (
        "7 of 14 short, 2 after ordinary evenings",
        "keep the evening under ~5.7 kWh",
    )


def test_november_afternoon_with_charging_over() -> None:
    s = summarize(
        facts(
            16,
            42,
            soc=66.0,
            peak=66,
            peak_cloudy=66,
            sunset_pct=66,
            this_morning={
                "said_full_at": at(12, 59).isoformat(),
                "solar_kwh": None,
                "forecast_kwh": 9.1,
            },
        )
    )

    assert row(s, "Battery").main == "No more charging today"
    assert row(s, "Battery").detail == ("this morning expected full ~12:59",)
    assert row(s, "Heavy loads").main == "No spare sun left today"
    assert row(s, "Surplus").main == "No surplus left today"


def test_on_the_grid_after_midnight() -> None:
    s = summarize(
        facts(
            0,
            48,
            soc=0.0,
            outlook="grid",
            reserve_at=at(0, 47, DAY + timedelta(days=1)),
            grid_kwh=2.6,
            full_at=at(13, 19, DAY + timedelta(days=1)),
        )
    )

    tonight = row(s, "Tonight")
    assert tonight.main == "On the grid now"
    assert tonight.detail == (
        "~2.6 kWh until the sun",
        "10 of the last 14 nights lasted",
    )
    assert s.label == "Night"


def test_an_evening_spending_its_budget() -> None:
    s = summarize(
        facts(20, 30, soc=80.0, evening_used=2.1, evening_budget=4.7, spare=3.2)
    )

    assert row(s, "Tonight").detail == (
        "~4.7 kWh left until midnight",
        "~3.2 kWh spare at sunrise",
    )
    assert s.label == "Evening"


def test_a_turned_night_says_what_it_was_and_why() -> None:
    s = summarize(
        facts(
            20,
            10,
            outlook="grid",
            reserve_at=at(23, 10) + timedelta(hours=4),
            grid_kwh=0.6,
            evening_used=3.9,
            evening_budget=0.0,
            changed_tonight={
                "was": {"outlook": "lasts", "grid_from": None, "spare_kwh": 0.8},
                "since": at(19, 52).isoformat(),
                "cause": "house used 1.1 kWh more than usual",
            },
        )
    )

    tonight = row(s, "Tonight")
    assert tonight.was == "Lasts the night"
    assert tonight.change == "since 19:52 · house used 1.1 kWh more than usual"


def test_last_nights_call_each_morning() -> None:
    call = {
        "said_spare_kwh": 0.0,
        "said_lasts": False,
        "spare_kwh": 0.0,
        "lasted": False,
        "right_calls": 2,
        "calls": 2,
    }

    morning = summarize(facts(8, 50, last_night=call))
    afternoon = summarize(facts(13, 0, last_night=call))

    last = row(morning, "Last night")
    assert last.main == "Needed the grid"
    assert last.detail == (
        "21:00 said it would need the grid",
        "2 of the last 2 calls right",
    )
    assert all(r.label != "Last night" for r in afternoon.rows)


# ── Every moment ──

MOMENTS = [(9, 0), (12, 30), (15, 30), (16, 45), (19, 30), (23, 0), (2, 0)]
MIX = {
    "none": (),
    "heavy": (7.5, 6.8, 7.9),
    "mixed": (7.5, 6.8, 4.1, 3.9),
    "most": (7.5, 6.8, 6.1, 4.1, 3.9, 4.4, 4.0, 3.8, 5.0),
}
MARGIN = {"comfortable": 6.5, "tight": 4.8, "over": 3.6}
# What the plan can produce together.
OUTLOOKS = {
    "lasts": (("none", "heavy"), ("comfortable",)),
    "borderline": (("heavy", "mixed", "most"), ("comfortable", "tight")),
    "grid": (("most",), ("tight", "over")),
}


def moments() -> list[tuple[str, Facts]]:
    out = []
    for (h, m), (
        outlook,
        (mixes, margins),
    ), left, battery, drift, window, money in itertools.product(
        MOMENTS,
        OUTLOOKS.items(),
        (None, 3.0, 0.6, 0.0),
        ("fills", "full", "peaks", "done"),
        ("none", "later", "earlier"),
        ("none", "started", "later"),
        (True, False),
    ):
        day = 8 <= h < 17
        if (
            (left is None) != (day or h < 12)
            or (outlook == "grid" and left == 3.0)
            or (outlook == "lasts" and left == 0.0)
        ):
            continue
        if battery == "done" and (not day or h < 15 or window != "none"):
            continue
        if battery == "full" and not day:
            continue
        if not day and (drift != "none" or window == "started"):
            continue
        for mix, margin in itertools.product(mixes, margins):
            f = facts(h, m)
            now = f.now
            soc = {"fills": 55.0, "full": 100.0, "peaks": 55.0, "done": 66.0}[battery]
            full_at = now + timedelta(hours=2) if battery == "fills" else None
            said = (
                (
                    now + timedelta(hours=1)
                    if drift == "later"
                    else now + timedelta(hours=3)
                )
                if full_at
                else now
            )
            f = replace(
                f,
                soc=soc,
                outlook=outlook,
                short_evenings=MIX[mix],
                lasting=14 - len(MIX[mix]),
                evening_budget=MARGIN[margin] if left is None else left,
                evening_used=None if left is None else 3.5,
                full_at=full_at,
                peak={"peaks": 78, "done": 66}.get(battery, 100),
                peak_cloudy={"peaks": 61, "done": 66}.get(battery, 88),
                sunset_pct=64 if battery == "done" else 92,
                reserve_at=now + timedelta(hours=5) if outlook == "grid" else None,
                heavy_at=now + timedelta(hours=6),
                grid_kwh=1.6 if outlook == "grid" else 0.0,
                grid_cost=0.49 if outlook == "grid" else 0.0,
                spare=1.4 if outlook == "lasts" else 0.2,
                window_start=None
                if window == "none"
                else now - timedelta(minutes=30)
                if window == "started"
                else now + timedelta(hours=1),
                window_end=None if window == "none" else now + timedelta(hours=3),
                window_kwh=6.3,
                free=battery == "full" and window != "none",
                free_until=now + timedelta(hours=2),
                surplus=4.2 if battery == "full" or window != "none" else 0.1,
                saving=0.97 if money else None,
                exported=0.32 if money else None,
                this_morning=None
                if drift == "none"
                else {
                    "said_full_at": said.isoformat(),
                    "solar_kwh": 6.4,
                    "forecast_kwh": 8.5,
                },
            )
            out.append(
                (
                    f"{h:02d}:{m:02d} {outlook} {mix} {margin} left={left} "
                    f"{battery} drift={drift} win={window}",
                    f,
                )
            )
    return out


CASES = moments()


def test_the_grid_of_moments_is_broad() -> None:
    assert len(CASES) > 1000


@pytest.mark.parametrize(("name", "f"), CASES[::7], ids=[n for n, _ in CASES[::7]])
def test_every_row_is_a_headline_and_two_short_clauses(name: str, f: Facts) -> None:
    for r in summarize(f).rows:
        assert len(r.main) <= HEADLINE_MAX, (name, r.main)
        assert len(r.detail) <= 2, (name, r.detail)
        for clause in r.detail:
            assert clause, name
            assert len(clause) <= CLAUSE_MAX, (name, clause)


def test_no_row_contradicts_itself_or_the_others() -> None:
    for name, f in CASES:
        s = summarize(f)
        tonight = row(s, "Tonight")
        text = " · ".join(tonight.detail)
        if tonight.main == "Lasts the night":
            assert "won't fit" not in text, name
        if tonight.main.startswith("Grid from") and s.phase != "day":
            assert "left until midnight" not in text, name
        battery = s.rows[0]
        if battery.main.startswith("Full ~") and "this morning expected" in " ".join(
            battery.detail
        ):
            said, fills = f.this_morning["said_full_at"], f.full_at  # type: ignore[index]
            reason = " ".join(battery.detail)
            if fills is not None and fills < datetime.fromisoformat(said):
                assert "less sun" not in reason, name
            if fills is not None and fills > datetime.fromisoformat(said):
                assert "more sun" not in reason, name
        if s.phase == "day" and battery.main == "No more charging today":
            assert row(s, "Heavy loads").main != "No spare sun today", name
        assert len(s.home_main) <= HEADLINE_MAX, name
