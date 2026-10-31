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
from homeassistant.helpers.sun import get_astral_event_date, get_astral_event_next
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from .const import (
    BACKTEST_EVENINGS,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_RESERVE,
    CONF_BATTERY_SOC,
    CONF_FEED_IN,
    CONF_GRID_POWER,
    CONF_HOUSE_ENERGY,
    CONF_IMPORT_POSITIVE,
    CONF_MAX_POWER,
    CONF_MIN_RESERVE,
    CONF_NIGHTS,
    CONF_PRICE,
    CONF_PRICE_ENTITY,
    CONF_ROUND_TRIP,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    DEFAULT_FEED_IN_CT,
    DEFAULT_MAX_POWER_KW,
    DEFAULT_MIN_RESERVE_PCT,
    DEFAULT_NIGHTS,
    DEFAULT_PRICE_CT,
    DEFAULT_ROUND_TRIP_PCT,
    DOMAIN,
    EVENT_FREE_POWER,
    HISTORY_MINUTE,
    LIVE_COOLDOWN_S,
    LIVE_INTERVAL_MIN,
)
from .core.backtest import EVENING, Evening, Score, Weighting, choose, score
from .core.battery import Battery, Rate
from .core.forecast import PvForecast
from .core.nights import HourlyUse, hourly_use, past_days, similarity_weights
from .core.plan import Plan, PlanInputs, plan
from .core.snapshot import to_snapshot

# The hour in progress counts towards "today so far" once this much of it is seen.
MIN_PARTIAL_HOUR = timedelta(minutes=15)

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
        # The house counter at the end of the last recorded hour, and when that was.
        self._counter_at: tuple[datetime, float] | None = None
        self.weighting = Weighting.EQUAL
        self.scores: dict[Weighting, Score] = {}
        self.evenings: list[Evening] = []
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
        # No export reads as 0.0, not -0.0.
        return max(-imported, 0.0) + 0.0

    def _house_reading(self) -> float | None:
        state = self._state(CONF_HOUSE_ENERGY)
        value = _number(state)
        if value is None or state is None:
            return None
        unit = (
            state.attributes.get("unit_of_measurement") or UnitOfEnergy.KILO_WATT_HOUR
        )
        try:
            return EnergyConverter.convert(value, unit, UnitOfEnergy.KILO_WATT_HOUR)
        except Exception:
            return None

    def _partial_kw(self, now: datetime) -> float | None:
        """The house's average power since the last recorded hour ended."""
        reading = self._house_reading()
        if self._counter_at is None or reading is None:
            return None
        since, counter = self._counter_at
        elapsed = now - since
        if not MIN_PARTIAL_HOUR <= elapsed <= timedelta(hours=2):
            return None
        # A reading below the counter means it reset at midnight: all of it is new.
        used = reading - counter if reading >= counter else reading
        return used / (elapsed.total_seconds() / 3600)

    # ── Prices ──

    def _entity_price(self) -> float | None:
        entity_id = self.config_entry.options.get(CONF_PRICE_ENTITY)
        state = self.hass.states.get(entity_id) if entity_id else None
        value = _number(state)
        if value is None or state is None or value < 0:
            return None
        unit = str(state.attributes.get("unit_of_measurement") or "").lower()
        # Cents per kWh (ct/kWh, c/kWh, cent/kWh), else a currency per kWh.
        return value / 100 if unit.startswith(("ct", "c/", "cent")) else value

    @property
    def price_source(self) -> str:
        """Where the price comes from: the entity while it has a value, else fixed."""
        return "entity" if self._entity_price() is not None else "fixed"

    @property
    def price(self) -> float:
        """What a kWh bought costs now, in currency per kWh.

        A price entity is read as it stands now and applied to every hour ahead;
        nothing is timed to cheap hours.
        """
        from_entity = self._entity_price()
        if from_entity is not None:
            return from_entity
        return self._option(CONF_PRICE, DEFAULT_PRICE_CT) / 100

    @property
    def feed_in(self) -> float:
        """What a kWh exported earns, in currency per kWh."""
        return self._option(CONF_FEED_IN, DEFAULT_FEED_IN_CT) / 100

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
        weights = None
        if self.weighting is Weighting.SIMILAR:
            days = past_days(self.use, self.tz, self.nights)
            weights = similarity_weights(
                self.use, self.tz, now, days, self._partial_kw(now)
            )
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
            weights=weights,
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
        house = self.config_entry.data[CONF_HOUSE_ENERGY]
        soc = self.config_entry.data[CONF_BATTERY_SOC]
        start = hour - timedelta(days=self.nights + BACKTEST_EVENINGS + 2)
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            hour,
            {house, soc},
            "hour",
            {"energy": UnitOfEnergy.KILO_WATT_HOUR},
            {"change", "state", "min"},
        )
        house_rows = rows.get(house, [])
        changes = {
            dt_util.utc_from_timestamp(row["start"]): change
            for row in house_rows
            if (change := row.get("change")) is not None
        }
        self.use = hourly_use(changes, self.tz)
        last = house_rows[-1] if house_rows else None
        self._counter_at = (
            (dt_util.utc_from_timestamp(last["end"]), last_state)
            if last is not None and (last_state := last.get("state")) is not None
            else None
        )
        lows = {
            dt_util.utc_from_timestamp(row["start"]): low
            for row in rows.get(soc, [])
            if (low := row.get("min")) is not None
        }
        self._choose_weighting(lows, now)
        self._use_until = hour

    def _evenings(self, lows: dict[datetime, float], now: datetime) -> list[Evening]:
        """What the battery did on recent nights, from its hourly lowest level."""
        out = []
        today = dt_util.as_local(now).date()
        for back in range(BACKTEST_EVENINGS, 0, -1):
            day = today - timedelta(days=back)
            sunrise = get_astral_event_date(
                self.hass, "sunrise", day + timedelta(days=1)
            )
            if sunrise is None or sunrise > now:
                continue
            start = datetime.combine(day, EVENING, self.tz)
            # The battery at 21:00: the lowest level in the hour before, as it drains.
            before = lows.get(start - timedelta(hours=1))
            night = [low for t, low in lows.items() if start <= t < sunrise]
            if before is None or not night:
                continue
            out.append(Evening(day, before, sunrise, min(night)))
        return out

    def _choose_weighting(self, lows: dict[datetime, float], now: datetime) -> None:
        try:
            battery = self.battery()
        except UpdateFailed:
            return
        evenings = self._evenings(lows, now)
        self.evenings = evenings
        self.weighting, self.scores = choose(
            lambda weighting: score(
                self.use, self.tz, evenings, battery, self.nights, weighting
            )
        )

    @property
    def score(self) -> Score | None:
        """How the night answer did on recent evenings, with the weighting in use."""
        return self.scores.get(self.weighting)

    # ── Updating ──

    async def _async_update_data(self) -> Plan:
        await self.async_load_use()
        previous: Plan | None = self.data
        result = plan(self.inputs())
        if result.free_power and not (previous is not None and previous.free_power):
            self.hass.bus.async_fire(EVENT_FREE_POWER, {})
        return result

    def snapshot(self) -> dict[str, Any]:
        """Everything the plan is made from right now, to replay it offline."""
        try:
            return to_snapshot(self.inputs(), self.evenings)
        except UpdateFailed as err:
            return {"error": str(err)}

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
        if price_entity := self.config_entry.options.get(CONF_PRICE_ENTITY):
            entity_ids.append(price_entity)
        return async_track_state_change_event(
            self.hass, entity_ids, self._async_changed
        )

    @callback
    def _async_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_request_refresh())
