"""The live plan, and the runtime that learns from history in the background."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.sun import get_astral_event_next
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_POWER,
    CONF_BATTERY_RESERVE,
    CONF_BATTERY_SOC,
    CONF_CHARGE_POSITIVE,
    CONF_COOK_THRESHOLD,
    CONF_DEFAULT_HOURS,
    CONF_DEFAULT_KWH,
    CONF_ENERGY_ENTITY,
    CONF_FEED_IN,
    CONF_FEED_IN_ENTITY,
    CONF_GRID_POWER,
    CONF_HOUSE_POWER,
    CONF_IMPORT_POSITIVE,
    CONF_MAX_KWH,
    CONF_MAX_POWER,
    CONF_MIN_RESERVE,
    CONF_NAME,
    CONF_POWER_ENTITY,
    CONF_PRICE,
    CONF_PRICE_ENTITY,
    CONF_SOLAR_ENERGY,
    CONF_SOLAR_POWER,
    CONF_SOLCAST_LATER,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    CONF_STATE_ENTITY,
    DEFAULT_COOK_THRESHOLD_KW,
    DEFAULT_FEED_IN_CT,
    DEFAULT_MAX_POWER_KW,
    DEFAULT_MIN_RESERVE_PCT,
    DEFAULT_PRICE_CT,
    DEFAULT_ROUND_TRIP,
    DOMAIN,
    EVENT_FREE_POWER,
    LIVE_COOLDOWN_S,
    LIVE_INTERVAL_MIN,
    RUNNING_STATES,
    SUBENTRY_APPLIANCE,
)
from .core.appliances import ApplianceProfile, RunTracker
from .core.battery import Battery
from .core.forecast import PvForecast
from .core.load import COOK_WINDOW, cycle_day
from .core.planner import ApplianceLoad, Plan, PlanInputs, battery_from, plan
from .store import ApplianceState, InsightsData, InsightsStore

_LOGGER = logging.getLogger(__name__)

# Above the cooking threshold this long, live, before the evening counts as cooked.
COOK_CONFIRM = timedelta(minutes=5)

type InsightsConfigEntry = ConfigEntry[InsightsCoordinator]


@dataclass(frozen=True, slots=True)
class Appliance:
    """An appliance from a subentry, with its configured defaults."""

    subentry_id: str
    name: str
    power_entity: str | None
    energy_entity: str | None
    state_entity: str | None
    base: ApplianceProfile

    @property
    def metered(self) -> bool:
        return self.energy_entity is not None or self.power_entity is not None


def _usable(state: State | None) -> bool:
    return state is not None and state.state not in (
        STATE_UNAVAILABLE,
        STATE_UNKNOWN,
        "",
    )


def _float(state: State | None) -> float | None:
    if not _usable(state):
        return None
    assert state is not None
    try:
        return float(state.state)
    except ValueError:
        return None


class InsightsCoordinator(DataUpdateCoordinator[Plan]):
    """Recomputes the plan when the battery, the sun or the forecast move."""

    config_entry: InsightsConfigEntry

    def __init__(self, hass: HomeAssistant, entry: InsightsConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=LIVE_INTERVAL_MIN),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=LIVE_COOLDOWN_S, immediate=True
            ),
        )
        self.store = InsightsStore(hass, entry.entry_id)
        self.inputs: PlanInputs | None = None
        self._cook_above_since: datetime | None = None
        self._unsubs: list[Callable[[], None]] = []

    # ── Configuration ──

    @property
    def data_store(self) -> InsightsData:
        return self.store.data

    @property
    def tz(self) -> tzinfo:
        return dt_util.get_default_time_zone()

    def entity(self, key: str) -> str | None:
        value = self.config_entry.data.get(key)
        return value if isinstance(value, str) and value else None

    def option(self, key: str, default: Any) -> Any:
        return self.config_entry.options.get(key, default)

    @property
    def appliances(self) -> list[Appliance]:
        out = []
        for subentry_id, sub in self.config_entry.subentries.items():
            if sub.subentry_type != SUBENTRY_APPLIANCE:
                continue
            data = sub.data
            out.append(
                Appliance(
                    subentry_id,
                    str(data.get(CONF_NAME) or sub.title),
                    data.get(CONF_POWER_ENTITY) or None,
                    data.get(CONF_ENERGY_ENTITY) or None,
                    data.get(CONF_STATE_ENTITY) or None,
                    ApplianceProfile(
                        float(data.get(CONF_DEFAULT_KWH, 1.0)),
                        float(data.get(CONF_DEFAULT_HOURS, 2.0)),
                        float(data.get(CONF_MAX_KWH, 6.0)),
                    ),
                )
            )
        return out

    def profile(self, appliance: Appliance) -> ApplianceProfile:
        return self.data_store.profile(appliance.subentry_id, appliance.base)

    # ── Reading states ──

    def state(self, entity_id: str | None) -> State | None:
        return self.hass.states.get(entity_id) if entity_id else None

    def number(self, key: str) -> float | None:
        return _float(self.state(self.entity(key)))

    def kw(self, entity_id: str | None) -> float | None:
        state = self.state(entity_id)
        value = _float(state)
        if value is None or state is None:
            return None
        unit = state.attributes.get("unit_of_measurement") or UnitOfPower.WATT
        try:
            return PowerConverter.convert(value, unit, UnitOfPower.KILO_WATT)
        except Exception:  # noqa: BLE001 - an odd unit is a missing reading
            return None

    def kwh(self, entity_id: str | None) -> float | None:
        state = self.state(entity_id)
        value = _float(state)
        if value is None or state is None:
            return None
        unit = (
            state.attributes.get("unit_of_measurement") or UnitOfEnergy.KILO_WATT_HOUR
        )
        try:
            return EnergyConverter.convert(value, unit, UnitOfEnergy.KILO_WATT_HOUR)
        except Exception:  # noqa: BLE001 - an odd unit is a missing reading
            return None

    def _eur_per_kwh(
        self, entity_key: str, option_key: str, default_ct: float
    ) -> float:
        state = self.state(self.config_entry.options.get(entity_key))
        value = _float(state)
        if value is not None and state is not None:
            unit = str(state.attributes.get("unit_of_measurement") or "")
            # A price in euros per kWh is below a few euros; one in cents is not.
            return value if unit.startswith(("€", "EUR")) else value / 100
        return float(self.option(option_key, default_ct)) / 100

    @property
    def price_eur_kwh(self) -> float:
        return self._eur_per_kwh(CONF_PRICE_ENTITY, CONF_PRICE, DEFAULT_PRICE_CT)

    @property
    def feed_in_eur_kwh(self) -> float:
        return self._eur_per_kwh(CONF_FEED_IN_ENTITY, CONF_FEED_IN, DEFAULT_FEED_IN_CT)

    @property
    def min_reserve_pct(self) -> float:
        return float(self.option(CONF_MIN_RESERVE, DEFAULT_MIN_RESERVE_PCT))

    @property
    def floor_pct(self) -> float:
        return max(self.number(CONF_BATTERY_RESERVE) or 0.0, self.min_reserve_pct)

    @property
    def capacity_kwh(self) -> float | None:
        """The battery's capacity, held through a sleep that reports none."""
        capacity = self.kwh(self.entity(CONF_BATTERY_CAPACITY))
        if capacity and capacity > 0:
            if capacity != self.data_store.capacity_kwh:
                self.data_store.capacity_kwh = capacity
                self.store.async_schedule_save()
            return capacity
        return self.data_store.capacity_kwh

    @property
    def round_trip(self) -> float:
        """Measured from the days recorded, within what batteries plausibly do."""
        charged = discharged = 0.0
        for record in self.data_store.records.values():
            if record.totals.charged and record.totals.discharged:
                charged += record.totals.charged
                discharged += record.totals.discharged
        if charged < 20:
            return DEFAULT_ROUND_TRIP
        return min(max(discharged / charged, 0.8), 0.99)

    def battery(self) -> Battery:
        return battery_from(
            self.capacity_kwh,
            self.number(CONF_BATTERY_SOC),
            self.number(CONF_BATTERY_RESERVE),
            self.min_reserve_pct,
            float(self.option(CONF_MAX_POWER, DEFAULT_MAX_POWER_KW)),
            self.round_trip,
        )

    def forecast(self) -> PvForecast:
        """Solcast today and tomorrow, with the learned correction once it earns it."""
        detailed = [
            (self.state(self.entity(key)) or _EMPTY).attributes.get("detailedForecast")
            for key in (CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW)
        ]
        raw = PvForecast.from_solcast(*detailed)
        return self.data_store.pv_bias.apply(raw, self.tz)

    def _signed_kw(self, key: str, positive_option: str) -> float | None:
        value = self.kw(self.entity(key))
        if value is None:
            return None
        return value if self.option(positive_option, True) else -value

    @property
    def export_kw(self) -> float:
        grid = self._signed_kw(CONF_GRID_POWER, CONF_IMPORT_POSITIVE)
        return max(-(grid or 0.0), 0.0)

    @property
    def battery_charge_kw(self) -> float | None:
        return self._signed_kw(CONF_BATTERY_POWER, CONF_CHARGE_POSITIVE)

    def appliance_kw(self) -> float:
        return sum(
            self.kw(a.power_entity) or 0.0 for a in self.appliances if a.power_entity
        )

    @property
    def cooked_tonight(self) -> bool:
        day = cycle_day(dt_util.utcnow(), self.tz).isoformat()
        return day in self.data_store.cooked

    # ── The plan ──

    def build_inputs(self, now: datetime | None = None) -> PlanInputs:
        now = now or dt_util.utcnow()
        soc = self.number(CONF_BATTERY_SOC)
        if soc is None:
            raise UpdateFailed("The battery's state of charge is not available")
        sunrise = get_astral_event_next(self.hass, "sunrise", now)
        sunset = get_astral_event_next(self.hass, "sunset", now)
        if sunrise is None or sunset is None:
            raise UpdateFailed("No sunrise or sunset at this location today")
        later = self.config_entry.data.get(CONF_SOLCAST_LATER) or []
        next_days = [
            value
            for value in (
                _float(self.state(e))
                for e in [self.entity(CONF_SOLCAST_TOMORROW), *later]
            )
            if value is not None
        ]
        today = dt_util.as_local(now).date()
        record_today = None
        soc_max = self.data_store.snapshots.get("soc", {})
        full_since = None
        if soc_max.get("day") == today.isoformat():
            record_today = soc_max
            full_since = (
                datetime.fromisoformat(record_today["full_at"])
                if record_today.get("full_at")
                else None
            )
        return PlanInputs(
            now=now,
            tz=self.tz,
            sunrise=sunrise,
            sunset=sunset,
            battery=self.battery(),
            pv=self.forecast(),
            load=self.data_store.load,
            solar_kw=self.kw(self.entity(CONF_SOLAR_POWER)) or 0.0,
            house_kw=self.kw(self.entity(CONF_HOUSE_POWER)) or 0.0,
            export_kw=self.export_kw,
            yield_today_kwh=self.kwh(self.entity(CONF_SOLAR_ENERGY)),
            forecast_today_kwh=_float(self.state(self.entity(CONF_SOLCAST_TODAY))),
            forecast_next_days=next_days,
            full_since=full_since,
            soc_max_today=record_today.get("max") if record_today else None,
            cooked_tonight=self.cooked_tonight,
            appliances=[
                ApplianceLoad(a.name, (p := self.profile(a)).kwh, p.hours)
                for a in self.appliances
            ],
            price_eur_kwh=self.price_eur_kwh,
            capacity_known=bool(self.capacity_kwh),
        )

    async def _async_update_data(self) -> Plan:
        previous = self.data
        self._track_soc_today()
        self.inputs = self.build_inputs()
        result = plan(self.inputs)
        if result.free_power and not (previous and previous.free_power):
            self.hass.bus.async_fire(
                EVENT_FREE_POWER,
                {
                    "export_kw": round(self.inputs.export_kw, 2),
                    "until": result.free_until.isoformat()
                    if result.free_until
                    else None,
                },
            )
        return result

    def _track_soc_today(self) -> None:
        """Remember today's highest state of charge and when it first got full."""
        soc = self.number(CONF_BATTERY_SOC)
        if soc is None:
            return
        now = dt_util.utcnow()
        day = dt_util.as_local(now).date().isoformat()
        seen = self.data_store.snapshots.get("soc")
        if not seen or seen.get("day") != day:
            seen = {"day": day, "max": soc, "full_at": None}
        changed = False
        if soc > seen["max"]:
            seen["max"], changed = soc, True
        if soc >= 99.5 and not seen.get("full_at"):
            seen["full_at"], changed = now.isoformat(), True
        if changed or self.data_store.snapshots.get("soc") is not seen:
            self.data_store.snapshots["soc"] = seen
            self.store.async_schedule_save()

    # ── Live listeners ──

    @callback
    def async_start(self) -> None:
        """Follow the states that move the plan and the appliance runs."""
        watched = {
            self.entity(key)
            for key in (
                CONF_BATTERY_SOC,
                CONF_BATTERY_CAPACITY,
                CONF_BATTERY_RESERVE,
                CONF_GRID_POWER,
                CONF_SOLCAST_TODAY,
                CONF_SOLCAST_TOMORROW,
            )
        } - {None}
        if watched:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass,
                    sorted(e for e in watched if e),
                    self._async_plan_input_changed,
                )
            )
        power = {self.entity(CONF_HOUSE_POWER)} | {
            a.power_entity for a in self.appliances if a.power_entity
        }
        power.discard(None)
        if power:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, sorted(e for e in power if e), self._async_power_changed
                )
            )

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _async_plan_input_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _async_power_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        now = dt_util.utcnow()
        for appliance in self.appliances:
            if appliance.power_entity == entity_id:
                self._feed_appliance(appliance, now)
        if entity_id == self.entity(CONF_HOUSE_POWER) or any(
            a.power_entity == entity_id for a in self.appliances
        ):
            self._watch_cooking(now)

    def _feed_appliance(self, appliance: Appliance, now: datetime) -> None:
        watts = (self.kw(appliance.power_entity) or 0.0) * 1000
        meter = self.kwh(appliance.energy_entity)
        state = self.data_store.appliances.setdefault(
            appliance.subentry_id, ApplianceState()
        )
        before = state.tracker.running
        run = state.tracker.feed(now, watts, meter)
        if run is not None:
            profile = self.profile(appliance)
            if profile.add(run):
                state.runs = profile.runs
                _LOGGER.debug(
                    "%s ran %.2f kWh in %.1f h", appliance.name, run.kwh, run.hours
                )
            self.store.async_schedule_save()
            self.async_update_listeners()
        elif state.tracker.running != before:
            self.store.async_schedule_save()
            self.async_update_listeners()

    def appliance_running(self, appliance: Appliance) -> bool:
        if appliance.state_entity:
            state = self.state(appliance.state_entity)
            if _usable(state):
                assert state is not None
                return state.state.lower().rsplit(".", 1)[-1] in RUNNING_STATES
        tracker = self.data_store.appliances.get(appliance.subentry_id)
        return bool(tracker and tracker.tracker.running)

    def _watch_cooking(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        if not COOK_WINDOW[0] <= local.hour < COOK_WINDOW[1]:
            self._cook_above_since = None
            return
        house = self.kw(self.entity(CONF_HOUSE_POWER))
        if house is None:
            return
        net = house - self.appliance_kw()
        threshold = float(self.option(CONF_COOK_THRESHOLD, DEFAULT_COOK_THRESHOLD_KW))
        if net < threshold or any(
            self.appliance_running(a) for a in self.appliances if not a.power_entity
        ):
            self._cook_above_since = None
            return
        self._cook_above_since = self._cook_above_since or now
        if now - self._cook_above_since >= COOK_CONFIRM:
            self.mark_cooked(now)

    def mark_cooked(self, now: datetime) -> None:
        if self.data_store.mark_cooked(cycle_day(now, self.tz)):
            self.store.async_schedule_save()
            self.hass.async_create_task(self.async_request_refresh())


_EMPTY = State("sensor.none", STATE_UNKNOWN)
