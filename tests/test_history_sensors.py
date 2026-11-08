"""The typical day, the sun against the forecast and the battery's size: what
the dashboard worked out for itself, now sensors."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant

from custom_components.solar_energy_insights import const
from tests.common import DAY, recent, record_house, set_states, setup_entry
from tests.test_achievements import ON, history
from tests.test_answers import evening_load

PREFIX = "sensor.solar_energy_insights_"
TYPICAL = f"{PREFIX}typical_day"
RATIO = f"{PREFIX}sun_against_the_forecast"
SIZE = f"{PREFIX}battery_size"


async def test_the_typical_day_has_each_hour_of_the_week(home: HomeAssistant) -> None:
    await record_house(home, recent(35), evening_load)
    set_states(home)

    await setup_entry(home)

    typical = home.states.get(TYPICAL)
    assert typical is not None
    week = typical.attributes["house"]
    assert len(week) == 168
    assert week[19] == pytest.approx(evening_load(19))
    assert float(typical.state) == pytest.approx(
        sum(evening_load(h) for h in range(24)), rel=0.01
    )


async def test_without_achievements_only_the_typical_day(home: HomeAssistant) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)

    await setup_entry(home)

    assert home.states.get(RATIO) is None
    assert home.states.get(SIZE) is None


def closed_day(back: int, **facts: object) -> tuple[str, dict[str, object]]:
    day = DAY - timedelta(days=back)
    return day.isoformat(), {"day": day.isoformat(), "house_kwh": 10.0, **facts}


async def test_the_sun_against_the_forecast_and_the_battery_size(
    home: HomeAssistant,
) -> None:
    await history(home, recent(14))
    set_states(home)
    entry = await setup_entry(home, ON)
    coordinator = entry.runtime_data
    full = (DAY - timedelta(days=1)).isoformat() + "T11:00:00+00:00"
    coordinator.memory.memory.facts.clear()
    coordinator.memory.memory.facts.update(
        dict(
            [
                *(
                    closed_day(
                        n,
                        forecast_kwh=20.0,
                        solar_kwh=18.0,
                        soc_max=100.0,
                        full_at=full,
                        night_lasted=n % 4 != 0,
                    )
                    for n in range(1, 13)
                ),
                closed_day(13, forecast_kwh=10.0, solar_kwh=4.0, soc_max=60.0),
            ]
        )
    )

    await coordinator.async_refresh()
    await home.async_block_till_done()

    ratio = home.states.get(RATIO)
    assert ratio is not None
    assert float(ratio.state) == pytest.approx(100 * (12 * 18 + 4) / (12 * 20 + 10))
    assert len(ratio.attributes["history"]) == 13
    size = home.states.get(SIZE)
    assert size is not None
    # 3 of 12 full days ran out.
    assert size.state == "sometimes_too_small"
    assert size.attributes["too_small_share"] == 25
    assert size.attributes["days"] == {"right": 9, "too_small": 3, "never_full": 1}


async def test_the_options_switch_them_on_with_achievements(
    home: HomeAssistant,
) -> None:
    await history(home, recent(14))
    set_states(home)

    await setup_entry(home, {**ON, const.CONF_ACHIEVEMENTS: True})

    size = home.states.get(SIZE)
    assert size is not None
    assert size.state in {
        "right_size",
        "sometimes_too_small",
        "too_small",
        "not_enough_days",
    }
