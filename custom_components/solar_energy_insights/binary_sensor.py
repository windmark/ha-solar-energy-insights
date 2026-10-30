"""Yes/no answers automations can trigger on."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Appliance, InsightsConfigEntry, InsightsCoordinator
from .entity import InsightsEntity


@dataclass(frozen=True, kw_only=True)
class InsightsBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[InsightsCoordinator], bool | None]
    attrs_fn: Callable[[InsightsCoordinator], dict[str, Any]] | None = None
    from_plan: bool = True


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


BINARY_SENSORS: tuple[InsightsBinaryDescription, ...] = (
    InsightsBinaryDescription(
        key="lasts_the_night",
        translation_key="lasts_the_night",
        is_on_fn=lambda c: c.data.lasts_night,
        attrs_fn=lambda c: {
            "any_evening": c.data.lasts_night_any_evening,
            "reserve_at": _iso(c.data.reserve_at),
        },
    ),
    InsightsBinaryDescription(
        key="free_power",
        translation_key="free_power",
        device_class=BinarySensorDeviceClass.POWER,
        is_on_fn=lambda c: c.data.free_power,
        attrs_fn=lambda c: {"until": _iso(c.data.free_until)},
    ),
    InsightsBinaryDescription(
        key="grid_mode",
        translation_key="grid_mode",
        is_on_fn=lambda c: c.data.grid_mode,
    ),
    InsightsBinaryDescription(
        key="cooked_tonight",
        translation_key="cooked_tonight",
        from_plan=False,
        is_on_fn=lambda c: c.cooked_tonight,
    ),
)

APPLIANCE_RUNNING = InsightsBinaryDescription(
    key="running",
    translation_key="running",
    device_class=BinarySensorDeviceClass.RUNNING,
    from_plan=False,
    is_on_fn=lambda _c: None,
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: InsightsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(InsightsBinarySensor(coordinator, d) for d in BINARY_SENSORS)
    for appliance in coordinator.appliances:
        async_add_entities(
            [ApplianceRunning(coordinator, APPLIANCE_RUNNING, appliance)],
            config_subentry_id=appliance.subentry_id,
        )


class InsightsBinarySensor(InsightsEntity, BinarySensorEntity):
    entity_description: InsightsBinaryDescription

    @property
    def available(self) -> bool:
        if self.entity_description.from_plan:
            return super().available and self.coordinator.data is not None
        return True

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self.coordinator) if attrs_fn else None


class ApplianceRunning(InsightsEntity, BinarySensorEntity):
    appliance: Appliance

    @property
    def is_on(self) -> bool:
        return self.coordinator.appliance_running(self.appliance)
