"""Badges: relative to the home, seasonal by hemisphere, never peeking ahead."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from custom_components.solar_energy_insights.core.achievements.badges import (
    CATALOGUE,
    evaluate,
)
from custom_components.solar_energy_insights.core.achievements.facts import (
    with_stretches,
)
from custom_components.solar_energy_insights.core.achievements.profile import profile_of
from custom_components.solar_energy_insights.core.achievements.rules import (
    Context,
    Memo,
    run,
)
from tests.core.achievements.homes import home, without
from tests.core.helpers import TZ

END = date(2026, 10, 31)
BERLIN, SYDNEY = 52.5, -33.9


def states(days, latitude=BERLIN, battery=10.0, kwp=10.0, relative_yield=1.0):
    profile = profile_of(days, battery, latitude, kwp, relative_yield)
    return {b.badge.id: b for b in evaluate(days, profile, TZ)}, profile


def test_every_badge_is_offered_to_a_home_that_measures_everything() -> None:
    got, _ = states(home(400, END))

    missing = {b.id for b in CATALOGUE} - set(got)
    # Only the badges needing the plan's saved advice.
    assert missing == {"steady_hand", "forecast_whisperer", "oracle"}


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
    small = states(home(200, END, kwp=4.0, house_kwh=3.6, battery_kwh=4.0), kwp=4.0)[0][
        "power_plant"
    ]
    large = states(
        home(200, END, kwp=15.0, house_kwh=13.5, battery_kwh=15.0), kwp=15.0
    )[0]["power_plant"]

    # The same home scaled up earns it on the same days.
    assert small.earned_on == large.earned_on
    assert small.count > 0


def test_homes_of_any_size_earn_their_tiers_at_a_similar_pace() -> None:
    small, _ = states(
        home(365, END, kwp=4.0, house_kwh=3.6, battery_kwh=4.0), battery=4.0, kwp=4.0
    )
    large, _ = states(
        home(365, END, kwp=15.0, house_kwh=13.5, battery_kwh=15.0),
        battery=15.0,
        kwp=15.0,
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
        if state.badge.grade is not None:
            assert (
                after[badge_id].earned_on[: len(state.earned_on)] == state.earned_on
            ), badge_id


def test_hidden_badges_keep_their_name_until_earned_and_are_rare() -> None:
    days = home(200, END)
    lucky = replace(days[-1], night_lasted=True, night_spare_kwh=0.01)

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


def test_without_a_known_array_no_per_kwp_badge_is_offered() -> None:
    got, _ = states(home(200, END), kwp=None)

    assert "power_plant" not in got
    assert "mwh_club" not in got
    # Badges ranking days against your own history need no size.
    assert "every_drop" in got
    assert "midsummer" in got


def test_a_roof_facing_away_from_the_sun_earns_power_plant_at_its_own_pace() -> None:
    # Exporting 1.8 kWh per kWp: short of an ideal roof's 2, a lot for east–west.
    days = [replace(f, export_kwh=18.0) for f in home(60, END)]

    south, _ = states(days, kwp=10.0, relative_yield=1.0)
    east_west, _ = states(days, kwp=10.0, relative_yield=0.81)

    # Short of bronze's 2 kWh on an ideal roof; 2.2 for east–west.
    assert south["power_plant"].tier == 0
    assert east_west["power_plant"].tier == 1
    assert east_west["power_plant"].step_days == (60, 0, 0)


def self_sufficient(share: float):
    """A day that took *share* of its use from home."""

    def tweak(f):
        return replace(f, import_kwh=round(f.house_kwh * (1 - share), 3))

    return tweak


WINTER_END = date(2026, 2, 15)


def winter(days: int, share: float, better: int = 0, best_share: float = 0.0):
    """*days* up to mid-February at *share* self-sufficiency, the last *better*
    of them at *best_share*."""
    out = home(days, WINTER_END, tweak=self_sufficient(share))
    return out[: len(out) - better] + [
        self_sufficient(best_share)(f) for f in out[len(out) - better :]
    ]


def test_each_tier_asks_for_a_better_day() -> None:
    got = states(winter(50, 0.82))[0]["winter_warrior"]

    # Bronze at 80 %; nothing yet towards silver's ten days at 85 %.
    assert got.tier == 1
    assert got.count == 0
    assert got.next_at == 10
    assert got.step_days is not None
    assert got.step_days[1:] == (0, 0)


def test_a_better_day_counts_for_every_step_below_it() -> None:
    nine = states(winter(50, 0.82, better=9, best_share=0.87))[0]["winter_warrior"]
    ten = states(winter(50, 0.82, better=10, best_share=0.87))[0]["winter_warrior"]
    best = states(winter(50, 0.82, better=30, best_share=0.95))[0]["winter_warrior"]

    assert (nine.tier, nine.count) == (1, 9)
    assert ten.tier == 2
    assert best.tier == 3
    assert best.step_days is not None
    assert best.step_days[0] >= best.step_days[1] >= best.step_days[2] == 30


def test_a_successor_is_locked_until_the_badge_before_is_complete() -> None:
    dark = home(400, END, tweak=lambda f: replace(f, night_lasted=False))

    got = states(dark)[0]

    assert got["harvest_night"].tier == 0
    assert got["harvest_moon"].locked
    assert got["harvest_moon"].count == 0


def test_a_successor_counts_only_the_days_after_it_was_unlocked() -> None:
    got = states(home(730, END))[0]

    night, moon = got["harvest_night"], got["harvest_moon"]
    assert night.tier == 3
    assert not moon.locked
    assert all(d > night.tier_days[-1] for d in moon.earned_on)
    # Its editions start then too: none before it could be played.
    assert moon.editions[0].start <= night.tier_days[-1] <= moon.editions[0].end
    assert moon.editions[0].partial


def test_a_run_is_graded_on_the_day_it_reaches_each_length() -> None:
    days = home(30, END, tweak=lambda f: replace(f, night_lasted=True))
    holds = run(lambda f, _c: f.night_lasted, (3, 5, 7))
    profile = profile_of(days, 10.0, BERLIN)

    grades = [holds(f, Context(profile, TZ, days[:k])) for k, f in enumerate(days[:8])]

    assert grades == [0, 0, 1, 0, 2, 0, 3, 0]


def test_a_seasonal_badge_is_played_in_editions() -> None:
    got = states(home(750, END))[0]["harvest_night"]

    labels = [e.label for e in got.editions]
    assert labels == ["2024", "2025", "2026"]
    # History began in the middle of the 2024 autumn.
    assert got.editions[0].partial
    assert sum(e.days for e in got.editions) == len(got.earned_on)


def test_low_sun_hero_ranks_winter_days_by_the_sky_they_had() -> None:
    """Against a clear sky, a bright December day counts as much as a bright
    March one; by yield alone, December can hardly compete."""
    days = home(800, END)
    by_sky = states(days)[0]["low_sun_hero"]
    by_yield = states([replace(f, clear_kwh=None) for f in days])[0]["low_sun_hero"]

    def dark(state):
        return sum(1 for d in state.earned_on if d.month in (12, 1))

    assert dark(by_sky) > dark(by_yield)


def test_bluebird_day_needs_the_planes() -> None:
    days = home(200, END)

    assert "bluebird_day" in states(days)[0]
    assert "bluebird_day" not in states([replace(f, clear_kwh=None) for f in days])[0]


def test_the_sun_sponge_counts_only_days_after_an_empty_night() -> None:
    days = home(200, END, varied=True)
    held = [replace(f, night_lasted=True) for f in days]

    sponge = states(held)[0]["sun_sponge"]
    # Not one day was a test, so none counts either way.
    assert sponge.count == 0
    assert sponge.applied == 0


def test_the_wheel_of_the_year_counts_months_with_a_grid_free_night() -> None:
    def summer_only(f):
        return replace(f, night_lasted=f.day.month in (5, 6, 7, 8))

    all_year = states(home(400, END, tweak=lambda f: replace(f, night_lasted=True)))
    summers = states(home(400, END, tweak=summer_only))

    assert all_year[0]["wheel_of_the_year"].count == 12
    assert summers[0]["wheel_of_the_year"].count == 4


def test_groundhog_day_repeats_last_years_yield() -> None:
    days = home(400, END)
    same = replace(days[-1], solar_kwh=days[-366].solar_kwh)
    sunny = max(days[-60:], key=lambda f: f.solar_kwh or 0)
    again = [*days[:-1], replace(same, solar_kwh=sunny.solar_kwh)]
    year_ago = replace(again[-366], solar_kwh=sunny.solar_kwh)
    again[-366] = year_ago

    got = states(again)[0]["groundhog_day"]

    assert END in got.earned_on


def test_a_successor_only_counts_runs_after_it_was_unlocked() -> None:
    """Every day perfect: Perfect day's gold comes on its 20th perfect day,
    and Perfect streak's runs start the day after, not before it."""
    perfect = home(
        120,
        END,
        tweak=lambda f: replace(
            f,
            import_kwh=0.0,
            soc_max=100.0,
            full_at=datetime(f.day.year, f.day.month, f.day.day, 12, tzinfo=TZ),
            forecast_kwh=(f.solar_kwh or 1) * 0.5,
        ),
    )

    got = states(perfect)[0]

    gold = got["perfect_day"].tier_days[-1]
    streak = got["perfect_streak"]
    assert streak.tier == 3
    assert streak.tier_days[0] == gold + timedelta(days=2)
    assert streak.tier_days[-1] == gold + timedelta(days=5)


def test_percentiles_kept_between_days_are_the_same_as_worked_out_afresh() -> None:
    days = home(400, END, varied=True)
    profile = profile_of(days, 10.0, BERLIN, 10.0)
    value = Context(profile, TZ, days).specific_yield
    memo = Memo()

    for k in (20, 150, 399):
        kept = Context(profile, TZ, days[:k], memo=memo)
        fresh = Context(profile, TZ, days[:k])
        assert kept.quantiles(value, (0.1, 0.5, 0.95), key="y") == fresh.quantiles(
            value, (0.1, 0.5, 0.95)
        )


def test_a_long_stretch_off_the_grid_counts_once() -> None:
    """Four summer days off the grid in a row: one stretch of 96 hours, counted
    on the day it ends, not once a day."""
    start = date(2026, 7, 10)

    def stretch(f):
        on = start <= f.day < start + timedelta(days=4)
        return replace(
            f,
            offgrid_run=24.0 if on else 6.0,
            offgrid_head=24.0 if on else 0.0,
            offgrid_tail=24.0 if on else 0.0,
        )

    days = with_stretches(home(120, date(2026, 8, 31), tweak=stretch))

    island = states(days)[0]["island_mode"]

    assert island.earned_on == (start + timedelta(days=4),)
    assert island.step_days == (1, 1, 1)


def test_an_unchanged_home_is_rarely_crowned_by_the_weather() -> None:
    """Five years of the same home under the same repeating sky: a crown asks
    for clearly more than the season before, so the weather alone gives few."""
    days = home(5 * 365, END, varied=True)

    got = states(days)[0]

    crowns = sum(b.crowns for b in got.values())
    assert crowns <= 2
    # Badges ranked against your own days are never crowned.
    for badge_id in ("low_sun_hero", "early_bird", "midsummer"):
        assert got[badge_id].crowns == 0
