"""A diagnostics download holds everything needed to replay the moment."""

from __future__ import annotations

import json

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
    assert len(evenings_from_snapshot(download)) == len(
        entry.runtime_data.score.nights if entry.runtime_data.score else []
    )


async def test_without_a_plan_the_download_says_why(home: HomeAssistant) -> None:
    set_states(home)
    entry = await setup_entry(home)
    home.states.async_set("sensor.battery_soc", "unavailable")

    download = await async_get_config_entry_diagnostics(home, entry)

    assert "error" in download["replay"]
