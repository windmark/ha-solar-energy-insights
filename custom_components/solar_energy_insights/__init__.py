"""Solar Energy Insights: plans the home battery against Solcast, learns the house."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import InsightsConfigEntry, InsightsCoordinator
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
    await coordinator.store.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    coordinator.async_start()
    coordinator.learner.async_start()
    entry.async_on_unload(coordinator.async_stop)
    entry.async_on_unload(coordinator.learner.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    entry.async_create_background_task(
        hass, coordinator.learner.async_catch_up(), f"{DOMAIN} catch up"
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: InsightsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: InsightsConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.store.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: InsightsConfigEntry) -> None:
    from .store import InsightsStore

    await InsightsStore(hass, entry.entry_id).async_remove()
