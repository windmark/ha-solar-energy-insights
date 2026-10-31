"""Charge needed, surplus, free power until, and how well the night answer did."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

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
    assert error.attributes["weighting"] in ("equal", "similar")
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
