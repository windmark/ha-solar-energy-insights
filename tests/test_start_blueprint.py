"""The blueprint that starts an appliance in the sun is one Home Assistant
accepts, and makes a valid automation once its inputs are filled in."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict

BLUEPRINT = (
    Path(__file__).parent.parent
    / "blueprints/automation/solar_energy_insights/start_appliance.yaml"
)
START = [{"action": "button.press", "target": {"entity_id": "button.dishwasher_start"}}]


def blueprint() -> Blueprint:
    return Blueprint(
        load_yaml_dict(str(BLUEPRINT)),
        expected_domain="automation",
        schema=BLUEPRINT_SCHEMA,
    )


def test_the_blueprint_is_valid_and_asks_for_the_appliance() -> None:
    bp = blueprint()

    assert set(bp.inputs) == {"good_time", "ready", "start"}
    assert bp.inputs["ready"]["default"] == ""


async def validated(hass: HomeAssistant, inputs: dict) -> Any:
    assert await async_setup_component(hass, "automation", {})
    filled = BlueprintInputs(
        blueprint(),
        {
            "use_blueprint": {
                "path": "solar_energy_insights/start_appliance.yaml",
                "input": inputs,
            }
        },
    )
    filled.validate()
    return await async_validate_config_item(hass, "test", filled.async_substitute())


async def test_filled_in_it_makes_a_valid_automation(hass: HomeAssistant) -> None:
    config = await validated(
        hass,
        {
            "good_time": "binary_sensor.dishwasher_good_time_to_start",
            "ready": "binary_sensor.dishwasher_remote_start",
            "start": START,
        },
    )

    assert config is not None
    assert config.validation_status.name == "OK", config.validation_error


async def test_without_ready_it_is_valid_too(hass: HomeAssistant) -> None:
    config = await validated(
        hass,
        {"good_time": "binary_sensor.dishwasher_good_time_to_start", "start": START},
    )

    assert config is not None
    assert config.validation_status.name == "OK", config.validation_error
