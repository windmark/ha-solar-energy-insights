"""Achievements read counters as they really are: with jumps, and optional extras."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.core import Event, HomeAssistant
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.solar_energy_insights import const
from custom_components.solar_energy_insights.achievements import FACTS_VERSION
from tests.common import recent, record_energy, set_states, setup_entry
from tests.test_achievements import (
    EXPORT,
    IMPORT,
    ON,
    SOLAR,
    bought,
    history,
    sold,
    sun,
)

DAYS = recent(40)
FIRST = DAYS[0]


def jumps_on_first_hour(rate):
    """A counter that first reported 0, then its lifetime total: the recorder
    sees one enormous hour."""

    def kwh(day: date, hour: int) -> float:
        return 11_671.0 if (day, hour) == (FIRST, 0) else rate(day, hour)

    return kwh


async def room(home: HomeAssistant, ws: WebSocketGenerator) -> dict[str, Any]:
    client = await ws(home)
    await client.send_json_auto_id({"type": f"{const.DOMAIN}/achievements"})
    reply = await client.receive_json()
    assert reply["success"], reply
    result: dict[str, Any] = reply["result"]
    return result


async def test_a_meter_total_read_as_one_hour_breaks_no_record(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await history(home, DAYS[:0])
    from tests.common import record_battery, record_house
    from tests.test_answers import draining, evening_load

    await record_house(home, DAYS, evening_load)
    await record_battery(home, DAYS, draining)
    await record_energy(home, SOLAR, DAYS, jumps_on_first_hour(sun))
    await record_energy(home, IMPORT, DAYS, bought)
    await record_energy(home, EXPORT, DAYS, jumps_on_first_hour(sold))
    set_states(home)
    await setup_entry(home, ON)

    r = await room(home, hass_ws_client)

    records = {x["id"]: x for x in r["records"]}
    assert records["best_day"]["value"] < 50
    assert records["export"]["value"] < 50
    # Nothing is learned from the jump: without Solcast's sites, the array's
    # size is simply unknown.
    assert r["profile"]["kwp"] is None


async def test_stored_facts_from_an_older_version_are_rebuilt_quietly(
    home: HomeAssistant,
) -> None:
    await history(home, DAYS)
    set_states(home)
    entry = await setup_entry(home, ON)
    memory = entry.runtime_data.memory.memory
    # Facts saved by an older version, with a day that is wrong.
    memory.facts_version = FACTS_VERSION - 1
    memory.facts[FIRST.isoformat()]["solar_kwh"] = 11_671.0
    events: list[Event] = []
    home.bus.async_listen(const.EVENT_ACHIEVEMENT, events.append)

    await entry.runtime_data.async_refresh_history()

    assert memory.facts_version == FACTS_VERSION
    assert memory.facts[FIRST.isoformat()]["solar_kwh"] < 50
    assert events == []
