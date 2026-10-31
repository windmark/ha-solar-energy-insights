"""Solar Energy Insights: will the battery fill, will it last the night, and when
to run heavy loads."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import InsightsConfigEntry, InsightsCoordinator
from .memory import MemoryStore
from .services import async_setup_services
from .websocket import async_setup_websocket

PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    async_setup_services(hass)
    async_setup_websocket(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: InsightsConfigEntry) -> bool:
    coordinator = InsightsCoordinator(hass, entry)
    await coordinator.memory.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_follow_states())
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: InsightsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: InsightsConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.memory.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: InsightsConfigEntry) -> None:
    await MemoryStore(hass, entry.entry_id).async_remove()
