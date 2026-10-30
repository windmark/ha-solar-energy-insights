"""Sensors: the plan's answers, the day so far, and what has been learned."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import Appliance, InsightsConfigEntry, InsightsCoordinator
from .core.bookkeeping import DayRecord, Verdict, score
from .entity import InsightsEntity
from .serialize import plan_dict

type Value = StateType | datetime
# Days looked back for the forecast accuracy and the sizing verdicts.
ACCURACY_DAYS = 30
SIZING_DAYS = 60
LEADS = ("day_ahead", "morning", "midday", "final")


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _recent(c: InsightsCoordinator, days: int) -> list[DayRecord]:
    return [record for _, record in sorted(c.data_store.records.items())][-days:]


def _accuracy(c: InsightsCoordinator) -> tuple[float | None, dict[str, Any]]:
    """Actual yield against the morning forecast, and the miss at each lead time."""
    records = _recent(c, ACCURACY_DAYS)
    ratios: list[float] = []
    leads: dict[str, Any] = {}
    for lead in LEADS:
        pairs = [
            (r.totals.solar, getattr(r.forecasts, lead))
            for r in records
            if r.totals.solar is not None and (getattr(r.forecasts, lead) or 0) > 0.5
        ]
        if len(pairs) < 3:
            leads[lead] = None
            continue
        rel = [actual / forecast for actual, forecast in pairs]
        leads[lead] = {
            "days": len(rel),
            "miss_pct": round(sum(abs(x - 1) for x in rel) / len(rel) * 100, 1),
            "bias_pct": round((sum(rel) / len(rel) - 1) * 100, 1),
        }
        if lead == "morning":
            ratios = rel
    value = round(sum(ratios) / len(ratios) * 100, 1) if ratios else None
    return value, {"by_lead_time": leads}


def _sizing(c: InsightsCoordinator) -> tuple[str | None, dict[str, Any]]:
    judged = [r for r in _recent(c, SIZING_DAYS) if r.night and r.night.verdict]
    counts = {str(v): 0 for v in Verdict}
    for record in judged:
        assert record.night is not None
        counts[str(record.night.verdict)] += 1
    latest = judged[-1] if judged else None
    attrs: dict[str, Any] = {"days": len(judged), **counts}
    if latest and latest.night:
        attrs["night_of"] = latest.day.isoformat()
        attrs["grid_kwh"] = _round(latest.night.grid_kwh, 2)
        attrs["export_after_full_kwh"] = _round(latest.night.export_after_full_kwh, 2)
    verdict = latest.night.verdict if latest and latest.night else None
    return (str(verdict) if verdict else None), attrs


def _score_today(c: InsightsCoordinator) -> tuple[int | None, dict[str, Any]]:
    totals = c.today_totals()
    if totals.house is None:
        return None, {}
    snap = c.data_store.snapshots.get("today") or {}
    today = dt_util.as_local(dt_util.utcnow()).date().isoformat()
    morning = snap.get("morning") if snap.get("day") == today else None
    result = score(totals, c.capacity_kwh, c.floor_pct, morning)
    return result.total, {"parts": result.parts}


def _upgrade(c: InsightsCoordinator) -> dict[str, Any]:
    return {k: v for k, v in c.data_store.upgrade.items() if k != "per_year_eur"}


@dataclass(frozen=True, kw_only=True)
class InsightsSensorDescription(SensorEntityDescription):
    value_fn: Callable[[InsightsCoordinator], Value]
    attrs_fn: Callable[[InsightsCoordinator], dict[str, Any]] | None = None
    # Whether the value comes from the live plan, so needs one.
    from_plan: bool = True


def _plan(c: InsightsCoordinator) -> Any:
    return c.data


SENSORS: tuple[InsightsSensorDescription, ...] = (
    InsightsSensorDescription(
        key="headline",
        translation_key="headline",
        value_fn=lambda c: _plan(c).headline,
        attrs_fn=lambda c: {
            k: v
            for k, v in plan_dict(_plan(c), curve=False).items()
            if k in ("subline", "phase", "tone", "grid_mode", "lines")
        },
    ),
    InsightsSensorDescription(
        key="battery_full_at",
        translation_key="battery_full_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: _plan(c).full_at,
    ),
    InsightsSensorDescription(
        key="battery_peak",
        translation_key="battery_peak",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda c: round(_plan(c).peak_pct),
        attrs_fn=lambda c: {"at": _plan(c).peak_at.isoformat()},
    ),
    InsightsSensorDescription(
        key="battery_at_sunset",
        translation_key="battery_at_sunset",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda c: _round(_plan(c).sunset_pct, 0),
    ),
    InsightsSensorDescription(
        key="reserve_reached_at",
        translation_key="reserve_reached_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: _plan(c).reserve_at,
    ),
    InsightsSensorDescription(
        key="night_load",
        translation_key="night_load",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda c: _round(_plan(c).night_load_kwh, 2),
    ),
    InsightsSensorDescription(
        key="night_grid",
        translation_key="night_grid",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda c: _round(_plan(c).night_grid_kwh, 2),
    ),
    InsightsSensorDescription(
        key="night_spare",
        translation_key="night_spare",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda c: _round(_plan(c).night_spare_kwh, 2),
    ),
    InsightsSensorDescription(
        key="best_window",
        translation_key="best_window",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: w.start if (w := _plan(c).best_window) else None,
        attrs_fn=lambda c: {
            "end": w.end.isoformat() if (w := _plan(c).best_window) else None,
            "spare_kwh": _round(w.kwh, 2) if w else None,
        },
    ),
    InsightsSensorDescription(
        key="sun_covers_house_at",
        translation_key="sun_covers_house_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: _plan(c).sun_covers_house_at,
    ),
    InsightsSensorDescription(
        key="self_sufficiency_today",
        translation_key="self_sufficiency_today",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        from_plan=False,
        value_fn=lambda c: _round(c.today_totals().self_sufficiency_pct),
        attrs_fn=lambda c: {"self_use_pct": _round(c.today_totals().self_use_pct)},
    ),
    InsightsSensorDescription(
        key="score_today",
        translation_key="score_today",
        state_class=SensorStateClass.MEASUREMENT,
        from_plan=False,
        value_fn=lambda c: _score_today(c)[0],
        attrs_fn=lambda c: _score_today(c)[1],
    ),
    InsightsSensorDescription(
        key="forecast_accuracy",
        translation_key="forecast_accuracy",
        native_unit_of_measurement=PERCENTAGE,
        from_plan=False,
        value_fn=lambda c: _accuracy(c)[0],
        attrs_fn=lambda c: _accuracy(c)[1],
    ),
    InsightsSensorDescription(
        key="battery_sizing",
        translation_key="battery_sizing",
        device_class=SensorDeviceClass.ENUM,
        options=[str(v) for v in Verdict],
        from_plan=False,
        value_fn=lambda c: _sizing(c)[0],
        attrs_fn=lambda c: _sizing(c)[1],
    ),
    InsightsSensorDescription(
        key="upgrade_value",
        translation_key="upgrade_value",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=0,
        from_plan=False,
        value_fn=lambda c: c.data_store.upgrade.get("per_year_eur"),
        attrs_fn=_upgrade,
    ),
    InsightsSensorDescription(
        key="load_model_error",
        translation_key="load_model_error",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        from_plan=False,
        value_fn=lambda c: _round(c.data_store.load.mae_kwh, 3),
        attrs_fn=lambda c: {
            "hours_scored": c.data_store.load.scored_hours,
            "cooking_evenings": c.data_store.load.n_cook,
            "quiet_evenings": c.data_store.load.n_quiet,
            "cooking_share_pct": _round(
                (s * 100) if (s := c.data_store.load.cook_share) is not None else None,
                0,
            ),
        },
    ),
    InsightsSensorDescription(
        key="forecast_correction",
        translation_key="forecast_correction",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        from_plan=False,
        value_fn=lambda c: _round(c.data_store.pv_bias.improvement_pct),
        attrs_fn=lambda c: {
            "active": c.data_store.pv_bias.active,
            "days_scored": c.data_store.pv_bias.scored_days,
        },
    ),
)

APPLIANCE_SENSORS: tuple[InsightsSensorDescription, ...] = (
    InsightsSensorDescription(
        key="energy_per_run",
        translation_key="energy_per_run",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        from_plan=False,
        value_fn=lambda _c: None,
    ),
    InsightsSensorDescription(
        key="run_duration",
        translation_key="run_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        from_plan=False,
        value_fn=lambda _c: None,
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: InsightsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(InsightsSensor(coordinator, d) for d in SENSORS)
    for appliance in coordinator.appliances:
        async_add_entities(
            [ApplianceSensor(coordinator, d, appliance) for d in APPLIANCE_SENSORS],
            config_subentry_id=appliance.subentry_id,
        )


class InsightsSensor(InsightsEntity, SensorEntity):
    entity_description: InsightsSensorDescription

    @property
    def available(self) -> bool:
        if self.entity_description.from_plan:
            return super().available and self.coordinator.data is not None
        return True

    @property
    def native_value(self) -> Value:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self.coordinator) if attrs_fn else None


class ApplianceSensor(InsightsEntity, SensorEntity):
    """What one run takes: learned from the plug, else the configured default."""

    entity_description: InsightsSensorDescription
    appliance: Appliance

    @property
    def native_value(self) -> float:
        profile = self.coordinator.profile(self.appliance)
        if self.entity_description.key == "energy_per_run":
            return round(profile.kwh, 2)
        return round(profile.hours, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        profile = self.coordinator.profile(self.appliance)
        return {"learned": profile.learned, "runs": len(profile.runs)}
