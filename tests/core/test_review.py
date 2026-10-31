"""Found in a full review: each test failed before its fix."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.solar_energy_insights.core.nights import (
    hourly_use,
    past_days,
)
from custom_components.solar_energy_insights.core.plan import plan
from tests.core.helpers import TZ, at
from tests.core.scenarios import SEASONS, heavy_evenings, history, inputs

SUMMER, WINTER = SEASONS


def test_history_older_than_the_nights_replayed_changes_nothing() -> None:
    """The day's typical load and the fill-in for missing hours come from the
    nights replayed, not from months back when the house used far more."""
    recent = history(SUMMER.day, heavy_evenings, count=14)
    months = {
        (SUMMER.day - timedelta(days=n), h): 2.5
        for n in range(15, 80)
        for h in range(24)
    }
    now = at(SUMMER.day, 9)

    short = plan(inputs(SUMMER, now, peak_kw=6, use=recent, soc=40, nights=14))
    long = plan(
        inputs(SUMMER, now, peak_kw=6, use={**months, **recent}, soc=40, nights=14)
    )

    assert long.charge == short.charge
    assert long.surplus == short.surplus
    assert long.night == short.night


def test_a_day_the_recorder_missed_is_not_replayed_as_a_night() -> None:
    use = history(WINTER.day, heavy_evenings, count=20)
    outage = WINTER.day - timedelta(days=3)
    gappy = {key: kwh for key, kwh in use.items() if key[0] != outage}

    days = past_days(gappy, TZ, 14, today=WINTER.day)

    assert 3 not in [d.days_ago for d in days]
    assert len(days) == 14


def test_a_big_solar_hour_is_not_mistaken_for_a_glitch() -> None:
    noon = datetime(2026, 6, 10, 10, tzinfo=UTC)

    made = hourly_use({noon: 16.5}, TZ, max_kwh=None)

    assert list(made.values()) == [16.5]


def test_one_day_of_history_still_answers_after_the_change() -> None:
    use = {(WINTER.day - timedelta(days=1), h): 0.5 for h in range(24)}

    result = plan(inputs(WINTER, at(WINTER.day, 21), peak_kw=0, use=use, soc=50))

    assert result.night is not None
    assert len(result.night.outcomes) == 1
