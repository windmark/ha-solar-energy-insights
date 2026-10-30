"""Sensors: when the battery fills, how the night goes, when to run heavy loads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import InsightsConfigEntry
from .core.plan import Plan
from .entity import InsightsEntity


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


@dataclass(frozen=True, kw_only=True)
class InsightsSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Plan], StateType | datetime]
    attrs_fn: Callable[[Plan], dict[str, Any]] | None = None


SENSORS: tuple[InsightsSensorDescription, ...] = (
    InsightsSensorDescription(
        key="battery_full_at",
        translation_key="battery_full_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: p.charge.full_at,
        attrs_fn=lambda p: {
            "cloudy": _iso(p.charge.full_at_cloudy),
            "peak": round(p.charge.peak_pct),
            "peak_cloudy": round(p.charge.peak_pct_cloudy),
        },
    ),
    InsightsSensorDescription(
        key="battery_at_sunset",
        translation_key="battery_at_sunset",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda p: (
            None if p.charge.sunset_pct is None else round(p.charge.sunset_pct)
        ),
    ),
    InsightsSensorDescription(
        key="nights_covered",
        translation_key="nights_covered",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda p: None if p.night is None else round(p.night.share * 100),
        attrs_fn=lambda p: (
            {}
            if p.night is None
            else {"nights": len(p.night.outcomes), "lasting": p.night.lasting}
        ),
    ),
    InsightsSensorDescription(
        key="reserve_reached_at",
        translation_key="reserve_reached_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: None if p.night is None else p.night.typical.reserve_at,
        attrs_fn=lambda p: (
            {} if p.night is None else {"heavy_night": _iso(p.night.heavy.reserve_at)}
        ),
    ),
    InsightsSensorDescription(
        key="spare_at_sunrise",
        translation_key="spare_at_sunrise",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda p: (
            None if p.night is None else round(p.night.typical.spare_kwh, 2)
        ),
        attrs_fn=lambda p: (
            {}
            if p.night is None
            else {
                "heavy_night": round(p.night.heavy.spare_kwh, 2),
                "grid_kwh": round(p.night.typical.grid_kwh, 2),
                "grid_kwh_heavy_night": round(p.night.heavy.grid_kwh, 2),
            }
        ),
    ),
    InsightsSensorDescription(
        key="best_window",
        translation_key="best_window",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: p.best_window.start if p.best_window else None,
        attrs_fn=lambda p: {
            "end": _iso(p.best_window.end) if p.best_window else None,
            "spare_kwh": round(p.best_window.kwh, 2) if p.best_window else None,
        },
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: InsightsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(InsightsSensor(entry.runtime_data, d) for d in SENSORS)


class InsightsSensor(InsightsEntity, SensorEntity):
    entity_description: InsightsSensorDescription

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.current)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self.current) if attrs_fn else None
