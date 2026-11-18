"""Old options are dropped, and a better reserve sensor is offered as a repair."""

from __future__ import annotations

from types import MappingProxyType

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_energy_insights import const
from custom_components.solar_energy_insights.repairs import (
    ApplianceFoundFlow,
    BetterReserveFlow,
    async_create_fix_flow,
)
from tests.common import ENTITIES, set_states, setup_entry

POWEROCEAN = "ef_powerocean_tcpmodbus"


async def test_options_an_earlier_version_had_are_dropped(home: HomeAssistant) -> None:
    set_states(home)

    entry = await setup_entry(
        home,
        {
            const.CONF_PRICE: 30.8,
            "battery_health": ["sensor.pack_soh"],
            "charge_power_limit": "sensor.limit",
        },
    )

    assert dict(entry.options) == {const.CONF_PRICE: 30.8}


def powerocean(home: HomeAssistant, effective: bool = True) -> tuple[str, str | None]:
    """A PowerOcean installation's plain reserve, and its effective one."""
    po_entry = MockConfigEntry(domain=POWEROCEAN)
    po_entry.add_to_hass(home)
    registry = er.async_get(home)
    plain = registry.async_get_or_create(
        "sensor",
        POWEROCEAN,
        "abc_min_soc_limit",
        config_entry=po_entry,
        suggested_object_id="powerocean_min_soc_limit",
    )
    better = None
    if effective:
        better = registry.async_get_or_create(
            "sensor",
            POWEROCEAN,
            "abc_battery_reserve_effective",
            config_entry=po_entry,
            suggested_object_id="powerocean_battery_reserve_effective",
        ).entity_id
        home.states.async_set(better, "10", {"unit_of_measurement": "%"})
    home.states.async_set(plain.entity_id, "5", {"unit_of_measurement": "%"})
    return plain.entity_id, better


async def setup_with_reserve(home: HomeAssistant, reserve: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=const.DOMAIN,
        title="Solar Energy Insights",
        data={**ENTITIES, const.CONF_BATTERY_RESERVE: reserve},
    )
    entry.add_to_hass(home)
    await home.config_entries.async_setup(entry.entry_id)
    await home.async_block_till_done()
    return entry


async def test_the_effective_reserve_is_offered_and_one_click_switches_to_it(
    home: HomeAssistant,
) -> None:
    set_states(home)
    plain, better = powerocean(home)
    entry = await setup_with_reserve(home, plain)
    issue_id = f"better_reserve_{entry.entry_id}"

    issue = ir.async_get(home).async_get_issue(const.DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable

    flow = await async_create_fix_flow(home, issue_id, issue.data)
    assert isinstance(flow, BetterReserveFlow)
    flow.hass = home
    shown = await flow.async_step_init()
    assert shown["type"] == "form"
    await flow.async_step_confirm({})
    await home.async_block_till_done()

    assert entry.data[const.CONF_BATTERY_RESERVE] == better
    assert ir.async_get(home).async_get_issue(const.DOMAIN, issue_id) is None


async def test_no_repair_when_the_reserve_is_already_the_best_there_is(
    home: HomeAssistant,
) -> None:
    set_states(home)
    _, better = powerocean(home)
    assert better is not None
    entry = await setup_with_reserve(home, better)

    assert (
        ir.async_get(home).async_get_issue(
            const.DOMAIN, f"better_reserve_{entry.entry_id}"
        )
        is None
    )


async def test_no_repair_for_a_battery_that_is_not_a_powerocean(
    home: HomeAssistant,
) -> None:
    set_states(home)

    entry = await setup_entry(home)

    assert (
        ir.async_get(home).async_get_issue(
            const.DOMAIN, f"better_reserve_{entry.entry_id}"
        )
        is None
    )


PLUG = "sensor.dishwasher_plug_energy"
ISSUE = "appliance_found_sensor_dishwasher_plug_energy"


def plug(home: HomeAssistant, object_id: str = "dishwasher_plug_energy") -> str:
    """A smart plug's all-time energy counter, on a device named for the dishwasher."""
    plug_entry = MockConfigEntry(domain="shelly")
    plug_entry.add_to_hass(home)
    device = dr.async_get(home).async_get_or_create(
        config_entry_id=plug_entry.entry_id,
        identifiers={("shelly", object_id)},
        name="Dishwasher plug",
    )
    entity = er.async_get(home).async_get_or_create(
        "sensor",
        "shelly",
        object_id,
        config_entry=plug_entry,
        device_id=device.id,
        suggested_object_id=object_id,
        original_device_class="energy",
    )
    home.states.async_set(
        entity.entity_id,
        "120.5",
        {"device_class": "energy", "state_class": "total_increasing"},
    )
    return entity.entity_id


async def fix(home: HomeAssistant, issue_id: str) -> None:
    issue = ir.async_get(home).async_get_issue(const.DOMAIN, issue_id)
    assert issue is not None
    flow = await async_create_fix_flow(home, issue_id, issue.data)
    assert isinstance(flow, ApplianceFoundFlow)
    flow.hass = home
    shown = await flow.async_step_init()
    assert shown["type"] == "form"
    await flow.async_step_confirm({})
    await home.async_block_till_done(wait_background_tasks=True)


async def test_an_appliance_counter_is_offered_and_one_click_adds_it(
    home: HomeAssistant,
) -> None:
    set_states(home)
    assert plug(home) == PLUG
    entry = await setup_entry(home)

    issue = ir.async_get(home).async_get_issue(const.DOMAIN, ISSUE)
    assert issue is not None
    assert issue.translation_key == "appliance_found"
    await fix(home, ISSUE)

    added = [s for s in entry.subentries.values() if s.subentry_type == "appliance"]
    assert [(s.title, s.data["energy"]) for s in added] == [("Dishwasher", PLUG)]
    assert ir.async_get(home).async_get_issue(const.DOMAIN, ISSUE) is None


async def test_an_appliance_of_the_same_name_gets_the_counter_not_a_twin(
    home: HomeAssistant,
) -> None:
    set_states(home)
    plug(home)
    entry = await setup_entry(home)
    home.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType({"name": "Dishwasher", "kwh": 1.2, "hours": 2.0}),
            subentry_type="appliance",
            title="Dishwasher",
            unique_id=None,
        ),
    )
    await home.config_entries.async_reload(entry.entry_id)
    await home.async_block_till_done(wait_background_tasks=True)

    issue = ir.async_get(home).async_get_issue(const.DOMAIN, ISSUE)
    assert issue is not None
    assert issue.translation_key == "appliance_counter"
    await fix(home, ISSUE)

    added = [s for s in entry.subentries.values() if s.subentry_type == "appliance"]
    assert [(s.title, s.data["kwh"], s.data["energy"]) for s in added] == [
        ("Dishwasher", 1.2, PLUG)
    ]


async def test_daily_meters_and_unrelated_counters_are_not_appliances(
    home: HomeAssistant,
) -> None:
    set_states(home)
    plug(home, "dishwasher_plug_energy_today")
    entry = await setup_entry(home)

    issues = [
        i
        for (domain, i) in ir.async_get(home).issues
        if domain == const.DOMAIN and i.startswith("appliance_found")
    ]
    assert issues == []
    assert entry.subentries == {}
