"""A diagnostics download holds everything needed to replay the moment."""

from __future__ import annotations

import json
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from custom_components.solar_energy_insights.core.plan import plan
from custom_components.solar_energy_insights.core.snapshot import (
    evenings_from_snapshot,
    from_snapshot,
)
from custom_components.solar_energy_insights.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.common import (
    DAY,
    local,
    recent,
    record_battery,
    record_house,
    set_states,
    setup_entry,
)
from tests.test_answers import draining, evening_load


async def test_a_download_replays_to_the_same_plan(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await record_house(home, recent(20), evening_load)
    await record_battery(home, recent(20), draining)
    freezer.move_to(local(DAY, 8, 20))
    set_states(home, soc=10, peak_kw=9)
    entry = await setup_entry(home)

    download = json.loads(
        json.dumps(await async_get_config_entry_diagnostics(home, entry))
    )

    assert plan(from_snapshot(download)) == entry.runtime_data.plan
    scored = entry.runtime_data.score
    assert scored is not None
    assert {n.day for n in scored.nights} <= {
        e.day for e in evenings_from_snapshot(download)
    }


async def test_the_download_replays_the_plan_shown_even_after_things_move_on(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Diagnostics are downloaded a while after the plan on screen was made:
    the replay must still be that plan, not one made from newer readings."""
    await record_house(home, recent(20), evening_load)
    freezer.move_to(local(DAY, 10, 18))
    set_states(home, soc=55, peak_kw=9)
    entry = await setup_entry(home)
    shown = entry.runtime_data.plan

    # Time passes and the house counter moves on, without a new plan yet.
    freezer.tick(timedelta(seconds=14))
    home.states.async_set(
        "sensor.house_energy",
        "1003.5",
        {"unit_of_measurement": "kWh", "device_class": "energy"},
    )
    download = json.loads(
        json.dumps(await async_get_config_entry_diagnostics(home, entry))
    )

    assert plan(from_snapshot(download)) == shown
