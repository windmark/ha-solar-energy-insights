"""The notification blueprint is one Home Assistant accepts, and makes a valid
automation once its inputs are filled in."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict

from custom_components.solar_energy_insights import const

BLUEPRINT = (
    Path(__file__).parent.parent
    / "blueprints/automation/solar_energy_insights/announce_achievements.yaml"
)


def blueprint() -> Blueprint:
    return Blueprint(
        load_yaml_dict(str(BLUEPRINT)),
        expected_domain="automation",
        schema=BLUEPRINT_SCHEMA,
    )


def test_the_blueprint_is_valid_and_listens_for_achievements() -> None:
    bp = blueprint()

    assert set(bp.inputs) == {"notify_device", "kinds"}
    assert bp.data["triggers"][0]["event_type"] == const.EVENT_ACHIEVEMENT


def test_it_offers_every_kind_the_integration_announces() -> None:
    options = blueprint().inputs["kinds"]["selector"]["select"]["options"]

    assert {o["value"] for o in options} == {
        "level",
        "badge",
        "record",
        "quest",
        "recap",
    }


async def test_filled_in_it_makes_a_valid_automation(hass: HomeAssistant) -> None:
    """Its trigger, condition and templates validate; the phone notification is
    the mobile app's own action, which needs the app's integration to check."""
    assert await async_setup_component(hass, "automation", {})
    inputs = BlueprintInputs(
        blueprint(),
        {
            "use_blueprint": {
                "path": "solar_energy_insights/announce_achievements.yaml",
                "input": {"notify_device": "abc123", "kinds": ["badge", "record"]},
            }
        },
    )
    inputs.validate()

    with patch(
        "homeassistant.components.device_automation.action.async_validate_action_config",
        side_effect=lambda _hass, config: config,
    ):
        config = await async_validate_config_item(
            hass, "test", inputs.async_substitute()
        )

    assert config is not None
    assert config.validation_status.name == "OK", config.validation_error
