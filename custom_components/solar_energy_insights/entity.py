"""Base entity: one service device for the plan, one device per appliance."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Appliance, InsightsCoordinator


class InsightsEntity(CoordinatorEntity[InsightsCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: InsightsCoordinator,
        description: EntityDescription,
        appliance: Appliance | None = None,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.appliance = appliance
        entry_id = coordinator.config_entry.entry_id
        if appliance is None:
            self._attr_unique_id = f"{entry_id}_{description.key}"
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry_id)},
                name="Solar Energy Insights",
                entry_type=DeviceEntryType.SERVICE,
            )
        else:
            self._attr_unique_id = f"{appliance.subentry_id}_{description.key}"
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, appliance.subentry_id)},
                name=appliance.name,
                entry_type=DeviceEntryType.SERVICE,
            )
