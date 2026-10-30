"""One service device holding every entity."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import InsightsCoordinator
from .core.plan import Plan


class InsightsEntity(CoordinatorEntity[InsightsCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: InsightsCoordinator, description: EntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Solar Energy Insights",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def current(self) -> Plan:
        """The plan; entities are only available while there is one."""
        plan = self.coordinator.plan
        assert plan is not None
        return plan

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.plan is not None
