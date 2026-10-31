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
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfTime
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
        state_class=SensorStateClass.MEASUREMENT,
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
        state_class=SensorStateClass.MEASUREMENT,
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
        state_class=SensorStateClass.MEASUREMENT,
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
            else p.charge_limit_pct
        ),
        attrs_fn=lambda p: (
            {}
            if p.needed is None
            else {
                "at": p.needed.at.isoformat(),
                "for": "sunset" if p.needed.at == p.sunset else "now",
                "typical_night": p.needed.typical_pct
                if p.needed.typical_pct is not None
                else p.charge_limit_pct,
                "charge_limit": p.charge_limit_pct,
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
        state_class=SensorStateClass.MEASUREMENT,
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


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _score_attrs(c: InsightsCoordinator) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "hidden_load_w": round(c.standby_kw * 1000),
        "heavy_share_pct": round(c.heavy_share * 100),
        "corrected_readings": c.corrected_readings,
    }
    cal = c.calibration
    if cal is not None:
        score = cal.score
        agreement = score.agreement
        attrs.update(
            {
                "honest_error_kwh": _r(cal.honest_error_kwh),
                "evenings_scored": len(score.nights),
                "contested_evenings": len(score.contested),
                "lasts_right_pct": None
                if agreement is None
                else round(agreement * 100),
                "bias_kwh": _r(score.bias_kwh),
                "bias_without_hidden_load_kwh": _r(cal.uncorrected.bias_kwh),
            }
        )
    if (live := c.live_nights) is not None:
        attrs.update(
            {
                "live_nights": live.nights,
                "live_error_kwh": _r(live.error_kwh),
                "live_lasts_right_pct": None
                if live.lasts_right is None
                else round(live.lasts_right * 100),
            }
        )
    return attrs


STATUS: tuple[InsightsStatusDescription, ...] = (
    InsightsStatusDescription(
        key="night_error",
        translation_key="night_error",
        state_class=SensorStateClass.MEASUREMENT,
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
        key="full_at_error",
        translation_key="full_at_error",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        # What the plan said in the morning, against when the battery got full.
        value_fn=lambda c: (
            None
            if c.live_full is None or c.live_full.error_minutes is None
            else round(c.live_full.error_minutes, 1)
        ),
        attrs_fn=lambda c: (
            {}
            if c.live_full is None
            else {"days": c.live_full.days, "missed": c.live_full.missed}
        ),
    ),
    InsightsStatusDescription(
        key="forecast_correction",
        translation_key="forecast_correction",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        # How much the roof's correction cuts Solcast's error on left-out days.
        value_fn=lambda c: _r(c.correction.improvement_pct, 1),
        attrs_fn=lambda c: {
            "active": c.correction.active,
            "days": c.correction.days,
            "raw_error_kwh": _r(c.correction.raw_error_kwh),
            "corrected_error_kwh": _r(c.correction.corrected_error_kwh),
            "factors_by_hour": [round(float(f), 2) for f in c.correction.factors],
        },
    ),
    InsightsStatusDescription(
        key="history_days",
        translation_key="history_days",
        state_class=SensorStateClass.MEASUREMENT,
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
    if coordinator.achievements.enabled:
        async_add_entities(GameSensor(coordinator, d) for d in GAME)


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


# ── Achievements ──


def _streak(key: str) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    return lambda v: next((s for s in v.get("streaks", []) if s["id"] == key), None)


def _open_quest(v: dict[str, Any]) -> dict[str, Any] | None:
    open_ = [q for q in v.get("quests", []) if q.get("result") is None]
    return open_[-1] if open_ else None


def _quest_title(v: dict[str, Any]) -> StateType:
    quest = _open_quest(v)
    return str(quest["title"]) if quest else None


STREAK_KEYS = ("self_sufficient", "full", "beat_forecast", "grid_free_night")


@dataclass(frozen=True, kw_only=True)
class GameDescription(SensorEntityDescription):
    """A piece of the trophy room, from the achievements view."""

    value_fn: Callable[[dict[str, Any]], StateType]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _streak_description(key: str) -> GameDescription:
    def current(v: dict[str, Any]) -> StateType:
        streak = _streak(key)(v)
        return int(streak["current"]) if streak else None

    def attrs(v: dict[str, Any]) -> dict[str, Any]:
        streak = _streak(key)(v) or {}
        return {k: streak.get(k) for k in ("best", "shields", "personal_best")}

    return GameDescription(
        key=f"streak_{key}",
        translation_key=f"streak_{key}",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="d",
        value_fn=current,
        attrs_fn=attrs,
    )


GAME: tuple[GameDescription, ...] = (
    GameDescription(
        key="score_today",
        translation_key="score_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda v: v["today"]["score"],
        attrs_fn=lambda v: {
            "parts": v["today"]["parts"],
            "last_day": v["last_day"],
        },
    ),
    GameDescription(
        key="level",
        translation_key="level",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda v: v["level"]["level"],
        attrs_fn=lambda v: {
            "level_name": v["level"]["name"],
            "xp": v["level"]["xp"],
            "next_xp": v["level"]["next_xp"],
            "progress": v["level"]["progress"],
        },
    ),
    GameDescription(
        key="rank",
        translation_key="rank",
        device_class=SensorDeviceClass.ENUM,
        options=["bronze", "silver", "gold", "platinum", "diamond"],
        value_fn=lambda v: v["rank"]["name"].lower(),
        attrs_fn=lambda v: {
            "average": v["rank"]["average"],
            "next_rank": v["rank"]["next_name"],
            "next_at": v["rank"]["next_at"],
            "season_factor": v["rank"]["season_factor"],
        },
    ),
    *(_streak_description(key) for key in STREAK_KEYS),
    GameDescription(
        key="quest",
        translation_key="quest",
        value_fn=_quest_title,
        attrs_fn=lambda v: {
            **(
                {k: q[k] for k in ("reward", "day", "until", "kind")}
                if (q := _open_quest(v))
                else {}
            ),
            "recent": [
                {"title": q["title"], "met": q["result"]}
                for q in v.get("quests", [])
                if q.get("result") is not None
            ][-5:],
        },
    ),
)


class GameSensor(InsightsEntity, SensorEntity):
    entity_description: GameDescription

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.achievements.view is not None

    @property
    def native_value(self) -> StateType:
        view = self.coordinator.achievements.view
        return self.entity_description.value_fn(view) if view else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        view = self.coordinator.achievements.view
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(view) if view and attrs_fn else None
