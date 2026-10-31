"""Badges: relative to the home, seasonal by hemisphere, never peeking ahead."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from custom_components.solar_energy_insights.core.achievements.badges import (
    CATALOGUE,
    evaluate,
)
from custom_components.solar_energy_insights.core.achievements.profile import profile_of
from tests.core.achievements.homes import home, without
from tests.core.helpers import TZ

END = date(2026, 10, 31)
BERLIN, SYDNEY = 52.5, -33.9


def states(days, latitude=BERLIN, battery=10.0):
    profile = profile_of(days, battery, latitude)
    return {b.badge.id: b for b in evaluate(days, profile, TZ)}, profile


def test_every_badge_is_offered_to_a_home_that_measures_everything() -> None:
    got, _ = states(home(400, END))

    missing = {b.id for b in CATALOGUE} - set(got)
    # Only the badges needing the plan's saved advice.
    assert missing == {"steady_hand", "forecast_whisperer"}


def test_a_home_without_a_solar_counter_sees_no_yield_badges() -> None:
    bare = home(
        200,
        END,
        tweak=without("solar_kwh", "solar_best_hour_kwh", "forecast_kwh", "peak_kw"),
    )

    got, _ = states(bare)

    assert "power_plant" not in got
    assert "mwh_club" not in got
    assert "harvest_night" in got


def test_power_plant_scales_with_the_array() -> None:
    small = states(home(200, END, kwp=4.0, house_kwh=3.6, battery_kwh=4.0))[0][
        "power_plant"
    ]
    large = states(home(200, END, kwp=15.0, house_kwh=13.5, battery_kwh=15.0))[0][
        "power_plant"
    ]

    # The same home scaled up earns it on the same days.
    assert small.earned_on == large.earned_on
    assert small.count > 0


def test_homes_of_any_size_earn_their_tiers_at_a_similar_pace() -> None:
    small, _ = states(
        home(365, END, kwp=4.0, house_kwh=3.6, battery_kwh=4.0), battery=4.0
    )
    large, _ = states(
        home(365, END, kwp=15.0, house_kwh=13.5, battery_kwh=15.0), battery=15.0
    )

    for badge_id in (
        "every_drop",
        "power_plant",
        "harvest_night",
        "winter_warrior",
        "silver_lining",
    ):
        assert small[badge_id].tier == large[badge_id].tier, badge_id


def test_seasonal_badges_follow_the_hemisphere() -> None:
    north, _ = states(home(365, END), BERLIN)
    south, _ = states(home(365, END, southern=True), SYDNEY)

    autumn_north = {d.month for d in north["harvest_night"].earned_on}
    autumn_south = {d.month for d in south["harvest_night"].earned_on}
    assert autumn_north <= {9, 10, 11, 12}
    assert autumn_south <= {3, 4, 5, 6}
    assert autumn_north
    assert autumn_south


def test_thresholds_never_peek_at_later_days() -> None:
    days = home(300, END)
    later = home(60, END + timedelta(days=60))

    before, _ = states(days)
    after, _ = states(days + later)

    for badge_id, state in before.items():
        if state.badge.earns is not None:
            assert (
                after[badge_id].earned_on[: len(state.earned_on)] == state.earned_on
            ), badge_id


def test_hidden_badges_keep_their_name_until_earned_and_are_rare() -> None:
    days = home(200, END)
    lucky = replace(days[-1], night_lasted=True, night_spare_kwh=0.05)

    got, _ = states([*days[:-1], lucky])

    finish = got["photo_finish"]
    assert finish.badge.hidden
    assert finish.count >= 1
    assert finish.rarity_name in ("rare", "legendary")


def test_advice_badges_need_the_plans_saved_answers() -> None:
    days = home(60, END)
    said = [replace(f, said_outlook="borderline") for f in days]

    got, _ = states(said)

    steady = got["steady_hand"]
    assert steady.count == sum(1 for f in said if f.night_lasted)


def test_tiers_record_the_day_they_were_reached() -> None:
    got, _ = states(home(400, END))

    for state in got.values():
        assert len(state.tier_days) == state.tier
        assert list(state.tier_days) == sorted(state.tier_days)
