"""One service device holding every entity, and one per appliance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_ICON
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .appliance import DEFAULT_ICON, appliance_run
from .const import DOMAIN
from .coordinator import InsightsCoordinator
from .core.appliances import Run, Start, best_start
from .core.plan import Plan

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry


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


class ApplianceEntity(InsightsEntity):
    """One of an appliance's entities, on the appliance's own device."""

    def __init__(
        self,
        coordinator: InsightsCoordinator,
        subentry: ConfigSubentry,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        entry_id = coordinator.config_entry.entry_id
        self.subentry = subentry
        self._attr_unique_id = f"{entry_id}_{subentry.subentry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            entry_type=DeviceEntryType.SERVICE,
        )
        self._attr_icon = subentry.data.get(CONF_ICON) or DEFAULT_ICON

    @property
    def run(self) -> Run:
        return appliance_run(self.hass, self.subentry.data)

    @property
    def start(self) -> Start | None:
        return best_start(self.current, self.run, dt_util.get_default_time_zone())

    def run_attributes(self) -> dict[str, Any]:
        run, start = self.run, self.start
        return {
            "kwh": round(run.kwh, 2),
            "hours": round(run.hours, 2),
            "estimated": run.estimated,
            "free_share": None if start is None else round(start.free_share * 100),
            "now_share": None
            if start is None or start.now_share is None
            else round(start.now_share * 100),
        }
