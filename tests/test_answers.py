"""Charge needed, surplus, free power until, and how well the night answer did."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from custom_components.solar_energy_insights import const
from tests.common import (
    DAY,
    local,
    recent,
    record_battery,
    record_house,
    set_states,
    setup_entry,
    state,
)

PREFIX = "solar_energy_insights_"


def evening_load(hour: int) -> float:
    return 0.9 if hour >= 18 or hour < 6 else 0.4


def draining(hour: int) -> float:
    """Full by early afternoon, then down about 6 points an hour until morning."""
    if 13 <= hour < 18:
        return 100.0
    if hour >= 18:
        return 100.0 - (hour - 17) * 6
    return max(64.0 - (hour + 1) * 6, 12.0)


async def test_charge_needed_is_what_gets_through_a_heavy_night(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 21))
    set_states(home, soc=30)

    await setup_entry(home)

    needed = float(state(home, f"sensor.{PREFIX}charge_needed_for_the_night"))
    assert 30 < needed <= 100
    assert state(home, f"binary_sensor.{PREFIX}battery_lasts_the_night") == "off"


async def test_sunshine_left_over_is_reported(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home, soc=90, peak_kw=9)

    await setup_entry(home)

    assert float(state(home, f"sensor.{PREFIX}solar_surplus_until_sunset")) > 0


async def test_free_power_says_until_when(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 12))
    set_states(home, soc=100, grid_w=-3000, peak_kw=9)

    await setup_entry(home)

    free = home.states.get(f"binary_sensor.{PREFIX}free_power")
    assert free is not None
    assert free.state == "on"
    assert free.attributes["until"] is not None


async def test_the_night_answer_is_scored_on_recent_nights(home: HomeAssistant) -> None:
    await record_house(home, recent(20), evening_load)
    await record_battery(home, recent(20), draining)
    set_states(home)

    await setup_entry(home)

    error = home.states.get(f"sensor.{PREFIX}night_prediction_error")
    assert error is not None
    assert float(error.state) >= 0
    assert error.attributes["evenings_scored"] > 0
    assert error.attributes["contested_evenings"] <= error.attributes["evenings_scored"]
    assert int(state(home, f"sensor.{PREFIX}days_of_history")) >= 20


async def test_in_the_morning_charge_needed_is_for_sunset(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 8, 20))
    set_states(home, soc=10, peak_kw=9)

    await setup_entry(home)

    needed = home.states.get(f"sensor.{PREFIX}charge_needed_for_the_night")
    assert needed is not None
    assert needed.attributes["for"] == "sunset"
    # About tonight, not the floor just because the sun is coming.
    assert float(needed.state) > 10
    spare = home.states.get(f"sensor.{PREFIX}spare_at_sunrise")
    assert spare is not None
    assert spare.attributes["heavy_night"] <= float(spare.state)


async def test_free_power_off_has_no_until(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home, soc=50)

    await setup_entry(home)

    free = home.states.get(f"binary_sensor.{PREFIX}free_power")
    assert free is not None
    assert free.state == "off"
    assert "until" not in free.attributes


def asleep_at_reserve(hour: int) -> float:
    """Drains overnight, and reads 0 % while asleep at the reserve before dawn."""
    if 13 <= hour < 18:
        return 100.0
    if hour >= 18:
        return 100.0 - (hour - 17) * 9
    return 0.0 if 3 <= hour < 7 else 40.0


async def test_impossible_battery_readings_are_corrected_and_counted(
    home: HomeAssistant,
) -> None:
    await record_house(home, recent(20), evening_load)
    await record_battery(home, recent(20), asleep_at_reserve)
    set_states(home)

    await setup_entry(home)

    error = home.states.get(f"sensor.{PREFIX}night_prediction_error")
    assert error is not None
    assert error.attributes["corrected_readings"] > 0


async def test_the_night_outlook_says_more_than_yes_or_no(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 21))
    set_states(home, soc=30)

    await setup_entry(home)

    assert state(home, f"sensor.{PREFIX}night_outlook") in (
        "lasts",
        "borderline",
        "grid",
    )


def hungry_nights(hour: int) -> float:
    return 2.5 if hour >= 18 or hour < 6 else 0.5


async def test_a_night_no_battery_covers_reads_100_and_says_how_short(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), hungry_nights)
    freezer.move_to(local(DAY, 21))
    set_states(home, soc=60)

    await setup_entry(home)

    needed = home.states.get(f"sensor.{PREFIX}charge_needed_for_the_night")
    assert needed is not None
    assert float(needed.state) == 100
    assert needed.attributes["full_battery_lasts"] is False
    assert needed.attributes["grid_even_when_full_kwh"] > 0


async def test_an_empty_battery_at_night_still_shows_the_nights_that_lasted(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """At 1 am with the battery at its reserve, no replayed night lasts from
    here; the nights that really lasted are shown beside that."""
    days = recent(20)
    await record_house(home, days, evening_load)
    await record_battery(home, days, draining)
    freezer.move_to(local(DAY, 1))
    set_states(home, soc=5)

    entry = await setup_entry(home)

    covered = home.states.get(f"sensor.{PREFIX}nights_covered")
    assert covered is not None
    assert covered.state == "0"
    real = sorted(entry.runtime_data.calibration.score.nights, key=lambda n: n.day)[
        -14:
    ]
    assert covered.attributes["recent_nights"] == len(real)
    assert covered.attributes["recent_nights_lasted"] == sum(
        n.actual_lasts for n in real
    )
    assert covered.attributes["recent_nights_lasted"] > 0
    assert covered.attributes["last_night_lasted"] is real[-1].actual_lasts


async def test_the_outlook_says_which_nights_ran_short_and_what_the_evening_may_use(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 12))
    set_states(home, soc=60)

    await setup_entry(home)

    outlook = home.states.get(f"sensor.{PREFIX}night_outlook")
    assert outlook is not None
    a = outlook.attributes
    assert a["budget_kwh"] > 0
    assert 0 <= a["evening_budget_kwh"] <= a["budget_kwh"]
    assert a["usual_evening_kwh"] > 0
    assert a["evening_used_kwh"] is None  # the sun is up
    for night in a["short_nights"]:
        assert set(night) == {"date", "evening_kwh", "late_kwh", "grid_from"}


async def test_the_evenings_use_follows_the_house_counter_from_sunset(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    # After sunset on a June evening.
    freezer.move_to(local(DAY, 22))
    set_states(home, soc=80)
    entry = await setup_entry(home)
    coordinator = entry.runtime_data
    assert coordinator.evening_used_kwh is not None

    home.states.async_set(
        "sensor.house_energy",
        "1002.5",
        {
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
        },
    )
    await coordinator.async_refresh()

    # The hours since sunset come from the recorder; the rise is the counter's.
    before = coordinator.memory.memory.evening["counter"]
    assert coordinator.evening_used_kwh == pytest.approx(1002.5 - before)
    outlook = home.states.get(f"sensor.{PREFIX}night_outlook")
    assert outlook is not None
    assert outlook.attributes["evening_used_kwh"] == coordinator.evening_used_kwh
    assert coordinator.evening_used_kwh >= 2.5


async def test_a_turned_night_shows_what_it_was_since_when_and_why(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    freezer.move_to(local(DAY, 22))
    set_states(home, soc=90)
    entry = await setup_entry(home)
    coordinator = entry.runtime_data
    outlook = f"sensor.{PREFIX}night_outlook"
    before = home.states.get(outlook)
    assert before is not None
    assert before.attributes["changed"] is None

    def counter(kwh: float) -> None:
        home.states.async_set(
            "sensor.house_energy",
            str(kwh),
            {
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing",
            },
        )

    # Ten minutes of a heavy load: the counter jumps and the battery drops.
    freezer.tick(timedelta(minutes=10))
    set_states(home, soc=25)
    counter(1003.0)
    await coordinator.async_refresh()

    after = home.states.get(outlook)
    assert after is not None
    assert after.state != before.state
    changed = after.attributes["changed"]
    assert changed["was"]["outlook"] == before.state
    assert changed["cause"].startswith("house used ")
    assert changed["cause"].endswith("kWh more than usual")

    # Half an hour later the turn is no longer shown: the new answer is simply
    # the answer. (The level is held still here, so the grid time drifts later
    # as the minutes pass; that is a change of its own, from the turned night.)
    freezer.tick(timedelta(minutes=35))
    await coordinator.async_refresh()
    assert all(c.was.outlook != before.state for c in coordinator.changes.values())


async def test_this_morning_shows_what_was_said_and_the_sun_so_far(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(14), evening_load)
    solar = "sensor.solar_total"

    def made(kwh: float) -> None:
        home.states.async_set(
            solar,
            str(kwh),
            {
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing",
            },
        )

    freezer.move_to(local(DAY, 3))
    made(5000.0)
    set_states(home, soc=40, peak_kw=8)
    entry = await setup_entry(home, {const.CONF_SOLAR_ENERGY: solar})
    coordinator = entry.runtime_data

    freezer.move_to(local(DAY, 8))
    await coordinator.async_refresh()
    freezer.move_to(local(DAY, 12))
    made(5006.0)
    await coordinator.async_refresh()

    full = home.states.get(f"sensor.{PREFIX}battery_full_at")
    assert full is not None
    morning = full.attributes["this_morning"]
    assert morning["said_at"].startswith(local(DAY, 8).date().isoformat())
    assert morning["solar_kwh"] == pytest.approx(6.0)
    assert morning["forecast_kwh"] > 0


async def test_last_night_shows_the_call_and_what_happened(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    days = recent(20)
    await record_house(home, days, evening_load)
    await record_battery(home, days, draining)
    freezer.move_to(local(DAY, 9))
    set_states(home, soc=40)
    entry = await setup_entry(home)
    coordinator = entry.runtime_data
    real = {n.day: n for n in coordinator.calibration.score.nights}
    yesterday = DAY - timedelta(days=1)
    assert yesterday in real

    # What the plan said at 21:00 on the last three evenings.
    for back, lasts in ((3, True), (2, False), (1, True)):
        day = DAY - timedelta(days=back)
        coordinator.memory.memory.nights[day.isoformat()] = {
            "at": local(day, 21).isoformat(),
            "spare_kwh": 0.8,
            "lasts": lasts,
        }

    call = coordinator.last_night()

    assert call is not None
    assert call["said_lasts"] is True
    assert call["said_spare_kwh"] == 0.8
    assert call["lasted"] is real[yesterday].actual_lasts
    assert call["calls"] == 3
    assert call["right_calls"] == sum(
        lasts == real[DAY - timedelta(days=back)].actual_lasts
        for back, lasts in ((3, True), (2, False), (1, True))
    )


async def test_no_call_for_last_night_until_it_is_known(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)

    entry = await setup_entry(home)

    assert entry.runtime_data.last_night() is None
