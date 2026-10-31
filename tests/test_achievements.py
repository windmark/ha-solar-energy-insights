"""Achievements in Home Assistant: switched on, the history is replayed, the
room is computed, and what is new is announced."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Event, HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.solar_energy_insights import const
from tests.common import (
    DAY,
    local,
    recent,
    record_battery,
    record_energy,
    record_house,
    set_states,
    setup_entry,
    state,
)
from tests.test_answers import draining, evening_load

PREFIX = "solar_energy_insights_"
SOLAR, IMPORT, EXPORT = (
    "sensor.solar_energy",
    "sensor.grid_import",
    "sensor.grid_export",
)
ON = {
    const.CONF_ACHIEVEMENTS: True,
    const.CONF_SOLAR_ENERGY: SOLAR,
    const.CONF_GRID_IMPORT_ENERGY: IMPORT,
    const.CONF_GRID_EXPORT_ENERGY: EXPORT,
}


def sun(day: date, hour: int) -> float:
    return (3.0 if day.toordinal() % 4 else 4.5) if 9 <= hour < 17 else 0.0


def bought(day: date, hour: int) -> float:
    return 0.3 if (hour < 6 and day.toordinal() % 3 == 0) else 0.0


def sold(_day: date, hour: int) -> float:
    return 1.0 if 12 <= hour < 15 else 0.0


async def history(home: HomeAssistant, days: list[date]) -> None:
    await record_house(home, days, evening_load)
    await record_battery(home, days, draining)
    await record_energy(home, SOLAR, days, sun)
    await record_energy(home, IMPORT, days, bought)
    await record_energy(home, EXPORT, days, sold)


async def test_switched_on_the_history_is_replayed_quietly(home: HomeAssistant) -> None:
    await history(home, recent(40))
    events: list[Event] = []
    home.bus.async_listen(const.EVENT_ACHIEVEMENT, events.append)
    set_states(home)

    entry = await setup_entry(home, ON)

    assert len(entry.runtime_data.memory.memory.facts) >= 39
    assert int(state(home, f"sensor.{PREFIX}solar_level")) >= 1
    assert state(home, f"sensor.{PREFIX}solar_rank") in (
        "bronze",
        "silver",
        "gold",
        "platinum",
        "diamond",
    )
    nights = home.states.get(f"sensor.{PREFIX}streak_grid_free_nights")
    assert nights is not None
    assert int(nights.state) >= 0
    # Everything replayed was already earned: nothing is announced.
    assert events == []


async def test_switched_off_there_is_no_game(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await record_house(home, recent(14), evening_load)
    set_states(home)
    await setup_entry(home)
    client = await hass_ws_client(home)

    await client.send_json_auto_id({"type": f"{const.DOMAIN}/achievements"})
    reply = await client.receive_json()

    assert home.states.get(f"sensor.{PREFIX}solar_level") is None
    assert not reply["success"]
    assert reply["error"]["code"] == "disabled"


async def test_the_room_is_served_for_the_view(
    home: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    await history(home, recent(40))
    set_states(home)
    await setup_entry(home, ON)
    client = await hass_ws_client(home)

    await client.send_json_auto_id({"type": f"{const.DOMAIN}/achievements"})
    room: dict[str, Any] = (await client.receive_json())["result"]

    assert {"battery", "solar", "grid"} <= set(room["profile"]["capabilities"])
    assert room["badges"]
    assert {r["id"] for r in room["records"]} >= {"best_day", "export", "thriftiest"}
    assert room["level"]["xp"] > 0
    assert room["months"]


async def test_a_new_record_is_announced_when_its_day_closes(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await history(home, recent(40))
    events: list[Event] = []
    home.bus.async_listen(const.EVENT_ACHIEVEMENT, events.append)
    set_states(home)
    await setup_entry(home, ON)

    # Today becomes the sunniest day there has been, and then closes.
    await record_house(home, [DAY], evening_load)
    await record_battery(home, [DAY], draining)
    await record_energy(home, SOLAR, [DAY], lambda _d, h: 7.0 if 9 <= h < 17 else 0.0)
    await record_energy(home, IMPORT, [DAY], bought)
    await record_energy(home, EXPORT, [DAY], sold)
    freezer.move_to(local(DAY + timedelta(days=1), 0, 10))
    async_fire_time_changed(home)
    await home.async_block_till_done(wait_background_tasks=True)

    records = [e.data for e in events if e.data["kind"] == "record"]
    assert any(r["record"] == "best_day" for r in records)
    assert all(e.data["title"] and e.data["message"] for e in events)


async def test_a_quest_is_set_in_the_evening(
    home: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await history(home, recent(40))
    freezer.move_to(local(DAY, 20, 30))
    set_states(home, soc=80)

    entry = await setup_entry(home, ON)

    quests = entry.runtime_data.memory.memory.quests
    assert quests
    quest = state(home, f"sensor.{PREFIX}quest")
    assert quest not in ("unknown", "unavailable")
