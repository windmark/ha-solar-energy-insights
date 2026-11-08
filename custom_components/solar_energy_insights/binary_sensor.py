"""Yes/no answers for automations."""

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

from .appliance import appliances
from .coordinator import InsightsConfigEntry
from .core.plan import Plan
from .entity import ApplianceEntity, InsightsEntity


@dataclass(frozen=True, kw_only=True)
class InsightsBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[Plan], bool | None]
    attrs_fn: Callable[[Plan], dict[str, Any]] | None = None


BINARY_SENSORS: tuple[InsightsBinaryDescription, ...] = (
    InsightsBinaryDescription(
        key="lasts_the_night",
        translation_key="lasts_the_night",
        # The middle night by load.
        is_on_fn=lambda p: None if p.night is None else p.night.typical.lasts,
        attrs_fn=lambda p: (
            {}
            if p.night is None
            else {
                "nights": len(p.night.outcomes),
                "lasting": p.night.lasting,
                "heavy_night_lasts": p.night.heavy.lasts,
            }
        ),
    ),
    InsightsBinaryDescription(
        key="free_power",
        translation_key="free_power",
        device_class=BinarySensorDeviceClass.POWER,
        is_on_fn=lambda p: p.free_power,
        # Only while it is free: an empty time would show as 1970.
        attrs_fn=lambda p: {"until": p.free_until.isoformat()} if p.free_until else {},
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: InsightsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        InsightsBinarySensor(entry.runtime_data, d) for d in BINARY_SENSORS
    )
    for subentry in appliances(entry):
        async_add_entities(
            [ApplianceGoodTimeSensor(entry.runtime_data, subentry, GOOD_TIME)],
            config_subentry_id=subentry.subentry_id,
        )


GOOD_TIME = BinarySensorEntityDescription(
    key="good_time", translation_key="appliance_good_time"
)


class ApplianceGoodTimeSensor(ApplianceEntity, BinarySensorEntity):
    """On while starting now takes at least half the run from free sun, and
    about as much as the best start today."""

    @property
    def is_on(self) -> bool:
        start = self.start
        return start is not None and start.good_now

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        start = self.start
        return {
            **self.run_attributes(),
            "best_start": None if start is None else start.at.isoformat(),
        }


class InsightsBinarySensor(InsightsEntity, BinarySensorEntity):
    entity_description: InsightsBinaryDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.current)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self.current) if attrs_fn else None
