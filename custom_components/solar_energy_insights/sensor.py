"""Sensors: when the battery fills, how the night goes, when to run heavy loads."""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfTime
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .appliance import appliances
from .const import CONF_FEED_IN_CAP, DOMAIN
from .coordinator import InsightsConfigEntry, InsightsCoordinator
from .core.achievements.facts import MAX_SPIKE_SHARE, MIN_SPIKE_HOURS
from .core.nights import days_covered
from .core.plan import Outlook, Plan
from .dashboard import discover_strings, history_start, string_kwp
from .entity import ApplianceEntity, InsightsEntity
from .serialize import costs
from .strings import backfill_energy, energy_today, power_kw
from .summary import changed, summary_for


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


@dataclass(frozen=True, kw_only=True)
class InsightsSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Plan], StateType | datetime]
    attrs_fn: Callable[[Plan], dict[str, Any]] | None = None
    # Attributes that need more than the plan: what the integration knows beside it.
    extra_fn: Callable[[Plan, InsightsCoordinator], dict[str, Any]] | None = None


def _battery_now(p: Plan, c: InsightsCoordinator) -> dict[str, Any]:
    """The level the plan starts from: the battery's last reading while it is
    switched off below its reserve and reports nothing."""
    return {
        "battery_now": round(p.curve[0][1], 1) if p.curve else None,
        "battery_remembered": "soc_pct" in c.remembered,
    }


def _tonight_extra(p: Plan, c: InsightsCoordinator) -> dict[str, Any]:
    return {
        **_evening(p, c),
        "changed": changed(c.changes.get("tonight"), "tonight"),
        "last_night": c.last_night(),
    }


def _full_extra(p: Plan, c: InsightsCoordinator) -> dict[str, Any]:
    return {
        "changed": changed(c.changes.get("full_at"), "full_at"),
        "this_morning": c.this_morning(p),
    }


def _recent_extra(p: Plan, c: InsightsCoordinator) -> dict[str, Any]:
    """The share is a replay from the battery as it is now; next to it, what
    really happened, so an empty battery at 1 am does not read as "no night
    ever lasts"."""
    return {} if p.night is None else c.recent_nights()


SENSORS: tuple[InsightsSensorDescription, ...] = (
    InsightsSensorDescription(
        key="battery_full_at",
        translation_key="battery_full_at",
        extra_fn=_full_extra,
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
        extra_fn=_tonight_extra,
        device_class=SensorDeviceClass.ENUM,
        options=[str(o) for o in Outlook],
        # Yes/no hides a night that lasts only if it is not a heavy one.
        value_fn=lambda p: None if p.night is None else str(p.night.outlook),
    ),
    InsightsSensorDescription(
        key="nights_covered",
        translation_key="nights_covered",
        extra_fn=_recent_extra,
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
        extra_fn=_battery_now,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        # When even a full battery falls short: 100 % and the shortfall, not
        # unknown, because that is the most useful thing it can say.
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
                "charge_limit_now": p.charge_limit_now_pct,
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


CLIPPING = InsightsSensorDescription(
    key="expected_clipping",
    translation_key="expected_clipping",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    suggested_display_precision=1,
    # With the battery as it runs by itself: what holding room for midday saves.
    value_fn=lambda p: round(p.surplus.clipped_kwh, 2),
    attrs_fn=lambda p: {
        "from": _iso(p.surplus.clip_from),
        "until": _iso(p.surplus.clip_until),
        "cloudy": round(p.surplus.clipped_cloudy_kwh, 2),
    },
    extra_fn=lambda _p, c: {"cap_kw": _r(c.export_cap_kw())},
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
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        # On evenings left out of any fitting.
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
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
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


CURTAILED = InsightsStatusDescription(
    key="curtailed_today",
    translation_key="curtailed_today",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    suggested_display_precision=1,
    # An estimate: the forecast less what was made, while held at the cap.
    value_fn=lambda c: None if c.curtailed is None else c.curtailed.kwh,
    attrs_fn=lambda c: {
        "minutes_at_cap": None
        if c.curtailed is None
        else round(c.curtailed.minutes_at_cap),
        "cap_kw": _r(c.export_cap_kw()),
    },
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: InsightsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(InsightsSensor(coordinator, d) for d in SENSORS)
    async_add_entities(InsightsStatusSensor(coordinator, d) for d in STATUS)
    if entry.options.get(CONF_FEED_IN_CAP):
        async_add_entities(
            [
                InsightsSensor(coordinator, CLIPPING),
                InsightsStatusSensor(coordinator, CURTAILED),
            ]
        )
    async_add_entities(InsightsCostSensor(coordinator, d) for d in COSTS)
    async_add_entities([SummarySensor(coordinator, SUMMARY)])
    async_add_entities([MorningForecastSensor(coordinator, MORNING)])
    async_add_entities([CheckpointsSensor(coordinator, CHECKPOINTS_SENSOR)])
    if coordinator.achievements.enabled:
        async_add_entities([SpikeShareSensor(coordinator, SPIKE_SHARE)])
    for found in discover_strings(coordinator.hass, entry):
        n, power = found["n"], found["power"]
        async_add_entities(
            [
                StringEnergySensor(coordinator, n, power),
                StringPowerPerKwpSensor(coordinator, n, power),
                StringYieldSensor(coordinator, n, power),
            ]
        )
    if coordinator.achievements.enabled:
        async_add_entities(GameSensor(coordinator, d) for d in GAME)
    for subentry in appliances(entry):
        async_add_entities(
            [ApplianceStartSensor(coordinator, subentry, APPLIANCE_START)],
            config_subentry_id=subentry.subentry_id,
        )


APPLIANCE_START = SensorEntityDescription(
    key="best_start",
    translation_key="appliance_best_start",
    device_class=SensorDeviceClass.TIMESTAMP,
)


class ApplianceStartSensor(ApplianceEntity, SensorEntity):
    """When to start the appliance so it takes the most from free sun, until
    sunset; after sunset, tomorrow's."""

    @property
    def native_value(self) -> datetime | None:
        start = self.start
        return None if start is None else start.at

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.run_attributes()


class InsightsSensor(InsightsEntity, SensorEntity):
    entity_description: InsightsSensorDescription

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.current)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        d = self.entity_description
        attrs = d.attrs_fn(self.current) if d.attrs_fn else None
        if d.extra_fn is not None:
            attrs = {**(attrs or {}), **d.extra_fn(self.current, self.coordinator)}
        return attrs


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
            "feed_in_source": self.coordinator.feed_in_source,
        }


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


def _evening(p: Plan, coordinator: InsightsCoordinator) -> dict[str, Any]:
    """Why the night looks as it does, and what tonight's evening may use."""
    night = p.night
    if night is None:
        return {}
    today = dt_util.as_local(p.now).date()
    short = sorted((o for o in night.outcomes if not o.lasts), key=lambda o: o.days_ago)
    return {
        "budget_kwh": night.budget_kwh,
        "evening_budget_kwh": night.evening_budget_kwh,
        "usual_evening_kwh": round(
            float(statistics.median(o.evening_kwh for o in night.outcomes)), 2
        ),
        "evening_used_kwh": coordinator.evening_used_kwh,
        "short_nights": [
            {
                "date": (today - timedelta(days=o.days_ago)).isoformat(),
                "evening_kwh": round(o.evening_kwh, 1),
                "late_kwh": round(o.late_kwh, 1),
                "grid_from": o.reserve_at.isoformat() if o.reserve_at else None,
            }
            for o in short
        ],
    }


SUMMARY = SensorEntityDescription(key="summary", translation_key="summary")


class SummarySensor(InsightsEntity, SensorEntity):
    """The phase of the day, with the dashboard's rows as attributes: each
    answer as a headline and at most two short clauses."""

    # Display-only and changing with every update: not worth recording.
    _unrecorded_attributes = frozenset(
        {"rows", "home", "time", "soc", "asleep", "ring"}
    )

    @property
    def native_value(self) -> StateType:
        summary = summary_for(self.coordinator)
        return None if summary is None else summary.phase

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        summary = summary_for(self.coordinator)
        if summary is None:
            return None
        data = summary.as_dict()
        data.pop("phase")
        return data


ENERGY_REFRESH = timedelta(minutes=5)
# Every write goes into the recorder: write a meaningful change at once and
# small drift at most this often.
SIGNIFICANT_KW_PER_KWP = 0.01
DRIFT_EVERY = timedelta(seconds=30)


class _StringSensor(InsightsEntity, SensorEntity):
    """One of a string's sensors: named by the string, e.g. "PV1 energy today"."""

    @callback
    def _async_source_changed(self, _event: Event[EventStateChangedData]) -> None:
        """The sensor it follows changed: write the new value, on the event loop."""
        self.async_write_ha_state()

    kind: str

    def __init__(self, coordinator: InsightsCoordinator, n: int, power: str) -> None:
        super().__init__(
            coordinator,
            SensorEntityDescription(
                key=f"pv{n}_{self.kind}", translation_key=f"string_{self.kind}"
            ),
        )
        self.n, self.power = n, power
        self._attr_translation_placeholders = {"string": f"PV{n}"}

    @property
    def available(self) -> bool:
        # Only the string's power is needed, not the plan.
        return self.coordinator.last_update_success

    @property
    def kwp(self) -> float | None:
        return string_kwp(self.coordinator.config_entry.options, self.n)


class StringEnergySensor(_StringSensor):
    kind = "energy_today"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _today: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_refresh, ENERGY_REFRESH)
        )
        await self._async_refresh()
        # Once, so charts on this sensor keep the string's past days.
        start = await history_start(self.hass, self.power)
        await backfill_energy(
            self.hass, self.power, self.entity_id, f"PV{self.n} energy", start
        )

    async def _async_refresh(self, _now: datetime | None = None) -> None:
        self._today = await energy_today(self.hass, self.power, dt_util.utcnow())
        self.async_write_ha_state()

    @property
    def native_value(self) -> StateType:
        return self._today


class StringPowerPerKwpSensor(_StringSensor):
    """The string's power now, per kWp of its panels: strings on the same roof
    should read about the same."""

    kind = "power_per_kwp"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "kW/kWp"
    _attr_suggested_display_precision = 3
    _written: float | None = None
    _written_at: datetime | None = None

    @callback
    def _async_source_changed(self, _event: Event[EventStateChangedData]) -> None:
        """Write a meaningful change at once, small drift at most every 30 s,
        and always a change in whether there is a value at all."""
        value, now = self.native_value, dt_util.utcnow()
        last, at = self._written, self._written_at
        if value == last:
            return
        if (
            value is None
            or last is None
            or at is None
            or abs(float(value) - last) >= SIGNIFICANT_KW_PER_KWP
            or now - at >= DRIFT_EVERY
        ):
            self._async_write()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._async_write()

    @callback
    def _async_write(self) -> None:
        """Write the state, remembering what was written and when."""
        value = self.native_value
        self._written = None if value is None else float(value)
        self._written_at = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Home Assistant writes the first state itself, right after this.
        value = self.native_value
        self._written = None if value is None else float(value)
        self._written_at = dt_util.utcnow()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.power], self._async_source_changed
            )
        )

    @property
    def available(self) -> bool:
        return (
            self.kwp is not None
            and power_kw(self.hass.states.get(self.power)) is not None
        )

    @property
    def native_value(self) -> StateType:
        kw, kwp = power_kw(self.hass.states.get(self.power)), self.kwp
        return None if kw is None or kwp is None else round(max(kw, 0.0) / kwp, 4)


class StringYieldSensor(_StringSensor):
    """Today's energy per kWp: the fair way to compare strings and days."""

    kind = "yield_today"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "kWh/kWp"
    _attr_suggested_display_precision = 2

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        energy = f"sensor.{DOMAIN}_pv{self.n}_energy_today"
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [energy], self._async_source_changed
            )
        )

    @property
    def available(self) -> bool:
        return self.kwp is not None

    @property
    def native_value(self) -> StateType:
        state = self.hass.states.get(f"sensor.{DOMAIN}_pv{self.n}_energy_today")
        kwp = self.kwp
        try:
            return (
                None
                if kwp is None or state is None
                else round(float(state.state) / kwp, 3)
            )
        except ValueError:
            return None


MORNING = SensorEntityDescription(
    key="morning_forecast",
    translation_key="morning_forecast",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    suggested_display_precision=1,
)
HISTORY_DAYS = 60


def _morning(day: str, slots: list[list[Any]]) -> dict[str, Any]:
    """One morning's forecast: its total (mo) and per local hour (moh), kWh."""
    hours = [0.0] * 24
    for slot in slots:
        start, p50 = datetime.fromisoformat(str(slot[0])), float(slot[1])
        hours[dt_util.as_local(start).hour] += p50 / 2
    return {"d": day, "mo": round(sum(hours), 2), "moh": [round(h, 3) for h in hours]}


class MorningForecastSensor(InsightsEntity, SensorEntity):
    """Solcast's forecast for today as it stood in the morning, with the past
    mornings as attributes: today (d, mo, moh) and history, oldest first."""

    _unrecorded_attributes = frozenset({"today", "history"})

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    def _days(self) -> list[dict[str, Any]]:
        saved = self.coordinator.memory.memory.forecasts
        return [_morning(day, slots) for day, slots in sorted(saved.items())][
            -HISTORY_DAYS:
        ]

    @property
    def native_value(self) -> StateType:
        today = dt_util.now().date().isoformat()
        return next((m["mo"] for m in self._days() if m["d"] == today), None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        days, today = self._days(), dt_util.now().date().isoformat()
        return {
            "today": next((m for m in days if m["d"] == today), {}),
            "history": [m for m in days if m["d"] != today],
        }


CHECKPOINTS_SENSOR = SensorEntityDescription(
    key="forecast_checkpoints", translation_key="forecast_checkpoints"
)


class CheckpointsSensor(InsightsEntity, SensorEntity):
    """The number of days scored so far, with today's and tomorrow's frozen
    forecasts and the scores per checkpoint as attributes."""

    _attr_native_unit_of_measurement = "days"
    _unrecorded_attributes = frozenset(
        {"today", "tomorrow", "scores", "needs_solar_counter"}
    )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> StateType:
        scored = (self.coordinator.checkpoints.scores or {}).get("06:00") or {}
        return int(scored.get("days", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.checkpoints.view()


SPIKE_SHARE = SensorEntityDescription(
    key="spike_grid_share",
    translation_key="spike_grid_share",
    native_unit_of_measurement=PERCENTAGE,
    entity_category=EntityCategory.DIAGNOSTIC,
    suggested_display_precision=1,
)


class SpikeShareSensor(InsightsEntity, SensorEntity):
    """The grid's share of the house's use in hours the battery carried the
    house: what covers load spikes while the inverter ramps the battery up.
    Hours importing no more than this share still count as off the grid."""

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> StateType:
        return round(self.coordinator.achievements.spike_share * 100, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        a = self.coordinator.achievements
        live = a.live
        return {
            "learned": a.spike_hours >= MIN_SPIKE_HOURS,
            "hours_seen": a.spike_hours,
            "cap_pct": round(MAX_SPIKE_SHARE * 100),
            "excused_today_kwh": None if live is None else live.spike_kwh,
        }
