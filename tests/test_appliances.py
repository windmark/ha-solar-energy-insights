"""Appliances learn what a run takes from their plug."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from tests.common import set_states, setup_entry, state
from tests.test_services import DRYER

ENERGY = "sensor.dryer_energy_per_run"
RUNNING = "binary_sensor.dryer_running"


def plug(home: HomeAssistant, watts: float) -> None:
    home.states.async_set(
        "sensor.dryer_power", str(watts), {"unit_of_measurement": "W"}
    )


async def run(
    home: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: int, watts: float
) -> None:
    for _ in range(minutes):
        freezer.tick(timedelta(minutes=1))
        plug(home, watts)
        # A reading that does not change is still a reading.
        plug(home, watts + 0.1)
        await home.async_block_till_done()


async def test_starts_from_the_configured_default(home: HomeAssistant) -> None:
    set_states(home)
    plug(home, 0)
    await setup_entry(home, subentries=[DRYER])

    assert float(state(home, ENERGY)) == 2.5
    assert state(home, RUNNING) == "off"


async def test_learns_a_run_from_the_plug(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    set_states(home)
    plug(home, 0)
    await setup_entry(home, subentries=[DRYER])

    await run(home, freezer, 90, 1200)
    assert state(home, RUNNING) == "on"
    await run(home, freezer, 20, 0)

    learned = home.states.get(ENERGY)
    assert learned is not None
    assert learned.attributes["learned"] is True
    # 1.2 kW for an hour and a half.
    assert 1.5 < float(learned.state) < 2.0
    assert state(home, RUNNING) == "off"
