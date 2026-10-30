"""Fixtures shared by the Home Assistant tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from tests.common import DAY, configure_home, local


@pytest.fixture
async def home(
    recorder_mock: object,  # noqa: ARG001 - must be set up before hass
    enable_custom_integrations: None,  # noqa: ARG001
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> AsyncGenerator[HomeAssistant]:
    """Home Assistant with the recorder, in Augsburg, on a June morning."""
    await configure_home(hass)
    freezer.move_to(local(DAY, 9))
    yield hass
    # Let jobs that read the recorder finish before it shuts down.
    await hass.async_block_till_done(wait_background_tasks=True)
