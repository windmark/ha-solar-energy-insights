"""Recomputes the plan when the battery or the forecast moves.

The only state kept is in memory and comes back from the recorder after a
restart: the house's hourly use over the last nights, re-read once an hour.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, tzinfo
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
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
    CONF_BATTERY_RESERVE,
    CONF_BATTERY_SOC,
    CONF_GRID_POWER,
    CONF_HOUSE_ENERGY,
    CONF_IMPORT_POSITIVE,
    CONF_MAX_POWER,
    CONF_MIN_RESERVE,
    CONF_NIGHTS,
    CONF_ROUND_TRIP,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    DEFAULT_MAX_POWER_KW,
    DEFAULT_MIN_RESERVE_PCT,
    DEFAULT_NIGHTS,
    DEFAULT_ROUND_TRIP_PCT,
    DOMAIN,
    EVENT_FREE_POWER,
    HISTORY_MINUTE,
    LIVE_COOLDOWN_S,
    LIVE_INTERVAL_MIN,
)
from .core.battery import Battery, Rate
from .core.forecast import PvForecast
from .core.nights import HourlyUse, hourly_use
from .core.plan import Plan, PlanInputs, plan

_LOGGER = logging.getLogger(__name__)

type InsightsConfigEntry = ConfigEntry[InsightsCoordinator]


def _number(state: State | None) -> float | None:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


class InsightsCoordinator(DataUpdateCoordinator[Plan]):
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
        self.use: HourlyUse = {}
        self._use_until: datetime | None = None
        # The last capacity reported, for a battery that reports none while asleep.
        self._capacity_kwh: float | None = None

    # ── Reading ──

    @property
    def tz(self) -> tzinfo:
        return dt_util.get_default_time_zone()

    def _state(self, key: str) -> State | None:
        entity_id = self.config_entry.data.get(key)
        return self.hass.states.get(entity_id) if entity_id else None

    def _option(self, key: str, default: float) -> float:
        return float(self.config_entry.options.get(key, default))

    @property
    def nights(self) -> int:
        return int(self._option(CONF_NIGHTS, DEFAULT_NIGHTS))

    def _capacity(self) -> float | None:
        state = self._state(CONF_BATTERY_CAPACITY)
        value = _number(state)
        if value and value > 0 and state is not None:
            unit = (
                state.attributes.get("unit_of_measurement")
                or UnitOfEnergy.KILO_WATT_HOUR
            )
            try:
                self._capacity_kwh = EnergyConverter.convert(
                    value, unit, UnitOfEnergy.KILO_WATT_HOUR
                )
            except Exception:
                _LOGGER.debug("Unreadable capacity unit %s", unit)
        return self._capacity_kwh

    def _export_kw(self) -> float:
        state = self._state(CONF_GRID_POWER)
        value = _number(state)
        if value is None or state is None:
            return 0.0
        unit = state.attributes.get("unit_of_measurement") or UnitOfPower.WATT
        try:
            kw = PowerConverter.convert(value, unit, UnitOfPower.KILO_WATT)
        except Exception:
            return 0.0
        imported = (
            kw if self.config_entry.options.get(CONF_IMPORT_POSITIVE, True) else -kw
        )
        return max(-imported, 0.0)

    def battery(self) -> Battery:
        soc = _number(self._state(CONF_BATTERY_SOC))
        if soc is None:
            raise UpdateFailed("The battery's state of charge is not available")
        capacity = self._capacity()
        if not capacity:
            raise UpdateFailed("The battery's capacity is not available")
        reserve = _number(self._state(CONF_BATTERY_RESERVE)) or 0.0
        return Battery(
            capacity_kwh=capacity,
            soc_pct=soc,
            floor_pct=max(
                reserve, self._option(CONF_MIN_RESERVE, DEFAULT_MIN_RESERVE_PCT)
            ),
            max_power_kw=self._option(CONF_MAX_POWER, DEFAULT_MAX_POWER_KW),
            round_trip=self._option(CONF_ROUND_TRIP, DEFAULT_ROUND_TRIP_PCT) / 100,
        )

    def forecast(self) -> PvForecast:
        return PvForecast.from_solcast(
            *(
                state.attributes.get("detailedForecast") if state else None
                for state in (
                    self._state(CONF_SOLCAST_TODAY),
                    self._state(CONF_SOLCAST_TOMORROW),
                )
            )
        )

    def inputs(self, extra: Rate | None = None) -> PlanInputs:
        now = dt_util.utcnow()
        return PlanInputs(
            now=now,
            tz=self.tz,
            sunrise=get_astral_event_next(self.hass, "sunrise", now),
            sunset=get_astral_event_next(self.hass, "sunset", now),
            battery=self.battery(),
            pv=self.forecast(),
            use=self.use,
            nights=self.nights,
            export_kw=self._export_kw(),
            extra=extra,
        )

    # ── History ──

    async def async_load_use(self) -> None:
        """Re-read the house's hourly use once the recorder has a new hour."""
        now = dt_util.utcnow()
        hour = now.replace(minute=0, second=0, microsecond=0)
        if now.minute < HISTORY_MINUTE:
            hour -= timedelta(hours=1)
        if self._use_until == hour:
            return
        entity_id = self.config_entry.data[CONF_HOUSE_ENERGY]
        start = hour - timedelta(days=self.nights + 2)
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            hour,
            {entity_id},
            "hour",
            {"energy": UnitOfEnergy.KILO_WATT_HOUR},
            {"change"},
        )
        changes = {
            dt_util.utc_from_timestamp(row["start"]): change
            for row in rows.get(entity_id, [])
            if (change := row.get("change")) is not None
        }
        self.use = hourly_use(changes, self.tz)
        self._use_until = hour

    # ── Updating ──

    async def _async_update_data(self) -> Plan:
        await self.async_load_use()
        previous: Plan | None = self.data
        result = plan(self.inputs())
        if result.free_power and not (previous is not None and previous.free_power):
            self.hass.bus.async_fire(EVENT_FREE_POWER, {})
        return result

    @property
    def plan(self) -> Plan | None:
        return self.data

    @callback
    def async_follow_states(self) -> Any:
        """Recompute when anything the plan reads changes; returns the unsubscribe."""
        entity_ids = [
            entity_id
            for key in (
                CONF_BATTERY_SOC,
                CONF_BATTERY_CAPACITY,
                CONF_BATTERY_RESERVE,
                CONF_GRID_POWER,
                CONF_SOLCAST_TODAY,
                CONF_SOLCAST_TOMORROW,
            )
            if (entity_id := self.config_entry.data.get(key))
        ]
        return async_track_state_change_event(
            self.hass, entity_ids, self._async_changed
        )

    @callback
    def _async_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_request_refresh())
