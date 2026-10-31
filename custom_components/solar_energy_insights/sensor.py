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
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import InsightsConfigEntry, InsightsCoordinator
from .core.nights import days_covered
from .core.plan import Outlook, Plan
from .entity import InsightsEntity
from .serialize import costs


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
        key="night_outlook",
        translation_key="night_outlook",
        device_class=SensorDeviceClass.ENUM,
        options=[str(o) for o in Outlook],
        # Yes/no hides a night that lasts only if it is not a heavy one.
        value_fn=lambda p: None if p.night is None else str(p.night.outlook),
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
        key="charge_needed",
        translation_key="charge_needed",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        # The lowest level from which even a heavy night lasts: at sunset in the
        # day, compare it with "battery at sunset"; at night it is needed now.
        # When even a full battery is short, 100 %, and how much would still
        # come from the grid: the most important thing it can say, not unknown.
        value_fn=lambda p: (
            None
            if p.needed is None
            else p.needed.heavy_pct
            if p.needed.heavy_pct is not None
            else 100.0
        ),
        attrs_fn=lambda p: (
            {}
            if p.needed is None
            else {
                "at": p.needed.at.isoformat(),
                "for": "sunset" if p.needed.at == p.sunset else "now",
                "typical_night": p.needed.typical_pct
                if p.needed.typical_pct is not None
                else 100.0,
                "full_battery_lasts": p.needed.heavy_pct is not None,
                "grid_even_when_full_kwh": round(p.needed.heavy_short_kwh, 2),
                "grid_even_when_full_typical_kwh": round(p.needed.typical_short_kwh, 2),
            }
        ),
    ),
    InsightsSensorDescription(
        key="surplus",
        translation_key="surplus",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda p: round(p.surplus.total_kwh, 2),
        attrs_fn=lambda p: {
            "after_full": round(p.surplus.after_full_kwh, 2),
            "while_charging": round(p.surplus.while_charging_kwh, 2),
            "cloudy": round(p.surplus.cloudy_kwh, 2),
        },
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


@dataclass(frozen=True, kw_only=True)
class InsightsStatusDescription(SensorEntityDescription):
    """Facts about the integration itself rather than the plan."""

    value_fn: Callable[[InsightsCoordinator], StateType]
    attrs_fn: Callable[[InsightsCoordinator], dict[str, Any]] | None = None


def _score_attrs(c: InsightsCoordinator) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "weighting": str(c.weighting),
        "hidden_load_w": round(c.standby_kw * 1000),
        "corrected_readings": c.corrected_readings,
    }
    cal = c.calibration
    if cal is None:
        return attrs
    for weighting, error in cal.honest_error_kwh.items():
        attrs[f"error_{weighting}"] = None if error is None else round(error, 2)
    score = cal.score
    bias, uncorrected = score.bias_kwh, cal.uncorrected.bias_kwh
    agreement = score.agreement
    attrs.update(
        {
            "evenings_scored": len(score.nights),
            "contested_evenings": len(score.contested),
            "lasts_right_pct": None if agreement is None else round(agreement * 100),
            "bias_kwh": None if bias is None else round(bias, 2),
            "bias_without_hidden_load_kwh": None
            if uncorrected is None
            else round(uncorrected, 2),
        }
    )
    return attrs


STATUS: tuple[InsightsStatusDescription, ...] = (
    InsightsStatusDescription(
        key="night_error",
        translation_key="night_error",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        # The honest error: on evenings left out of any fitting.
        value_fn=lambda c: (
            None
            if c.calibration is None or c.calibration.error_kwh is None
            else round(c.calibration.error_kwh, 3)
        ),
        attrs_fn=_score_attrs,
    ),
    InsightsStatusDescription(
        key="history_days",
        translation_key="history_days",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="d",
        value_fn=lambda c: days_covered(c.use),
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: InsightsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(InsightsSensor(coordinator, d) for d in SENSORS)
    async_add_entities(InsightsStatusSensor(coordinator, d) for d in STATUS)
    async_add_entities(InsightsCostSensor(coordinator, d) for d in COSTS)


class InsightsSensor(InsightsEntity, SensorEntity):
    entity_description: InsightsSensorDescription

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.current)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self.current) if attrs_fn else None


class InsightsStatusSensor(InsightsEntity, SensorEntity):
    entity_description: InsightsStatusDescription

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self.coordinator) if attrs_fn else None


@dataclass(frozen=True, kw_only=True)
class InsightsCostDescription(SensorEntityDescription):
    """An amount of money from the plan's costs, with what it is made of."""

    cost: str
    attrs: dict[str, str]


COSTS: tuple[InsightsCostDescription, ...] = (
    InsightsCostDescription(
        key="night_grid_cost",
        translation_key="night_grid_cost",
        cost="night_grid",
        attrs={"heavy_night": "night_grid_heavy"},
    ),
    InsightsCostDescription(
        key="surplus_saving",
        translation_key="surplus_saving",
        cost="surplus_saving",
        attrs={"if_exported": "surplus_export_value"},
    ),
)


class InsightsCostSensor(InsightsEntity, SensorEntity):
    """Money, in the currency set in Home Assistant, at the configured prices."""

    entity_description: InsightsCostDescription
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.currency

    def _costs(self) -> dict[str, Any]:
        return costs(self.current, self.coordinator.price, self.coordinator.feed_in)

    @property
    def native_value(self) -> float | None:
        value: float | None = self._costs()[self.entity_description.cost]
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        values = self._costs()
        return {
            **{
                name: values[key] for name, key in self.entity_description.attrs.items()
            },
            "price": values["price"],
            "feed_in": values["feed_in"],
            "price_source": self.coordinator.price_source,
        }
