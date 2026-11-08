"""Reading the battery, prices and counters, with the last good readings kept
for when the battery is switched off; the array and the forecast."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import (
    get_last_state_changes,
    state_changes_during_period,
)
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import (
    State,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from .array import array_of
from .const import (
    CONF_ARRAY_KWP,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_RESERVE,
    CONF_BATTERY_SOC,
    CONF_CHARGE_LIMIT,
    CONF_FEED_IN,
    CONF_FEED_IN_CAP,
    CONF_FEED_IN_ENTITY,
    CONF_GRID_POWER,
    CONF_HOUSE_ENERGY,
    CONF_IMPORT_POSITIVE,
    CONF_MAX_CHARGE,
    CONF_MAX_POWER,
    CONF_MIN_RESERVE,
    CONF_NIGHTS,
    CONF_PRICE,
    CONF_PRICE_ENTITY,
    CONF_ROUND_TRIP,
    CONF_SOLAR_ENERGY,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    DEFAULT_FEED_IN_CT,
    DEFAULT_MAX_CHARGE_PCT,
    DEFAULT_MAX_POWER_KW,
    DEFAULT_MIN_RESERVE_PCT,
    DEFAULT_NIGHTS,
    DEFAULT_PRICE_CT,
    DEFAULT_ROUND_TRIP_PCT,
)
from .core.array import Array
from .core.battery import Battery
from .core.forecast import PvForecast
from .strings import power_kw

# A state of charge below this, with a floor above it, is a sleeping battery.
SLEEPING_READING_PCT = 0.5
# The hour in progress counts towards "today so far" once this much of it is seen.
MIN_PARTIAL_HOUR = timedelta(minutes=15)
# A charge limit lowered for part of the day, such as by an automation keeping
# room for midday, is not what the battery is meant to reach by sunset: the plan
# uses the highest the limit has been over this long.
LIMIT_WINDOW = timedelta(hours=24)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import InsightsConfigEntry
    from .core.correction import Correction
    from .memory import MemoryStore

_LOGGER = logging.getLogger(__name__)


def _number(state: State | None) -> float | None:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


class ReadingsMixin:
    """The coordinator's readings of the house: what it reads, and how."""

    hass: HomeAssistant
    config_entry: InsightsConfigEntry
    memory: MemoryStore
    remembered: set[str]
    correction: Correction
    # The charge limit's values over the last day: (since, %).
    limits_seen: list[tuple[datetime, float]]

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
                return self._keep_reading(
                    "capacity_kwh",
                    EnergyConverter.convert(value, unit, UnitOfEnergy.KILO_WATT_HOUR),
                )
            except Exception:
                _LOGGER.debug("Unreadable capacity unit %s", unit)
        return self._kept_reading("capacity_kwh")

    def _keep_reading(self, key: str, value: float) -> float:
        """Keep a good reading, so it outlives the battery switching off."""
        readings = self.memory.memory.battery
        if readings.get(key) != value:
            readings[key] = value
            self.memory.async_save_later()
        self.remembered.discard(key)
        return value

    def _kept_reading(self, key: str) -> float | None:
        """The last good reading, while the battery reports none."""
        value = self.memory.memory.battery.get(key)
        if value is not None:
            self.remembered.add(key)
        return value

    async def async_remember_from_recorder(self) -> None:
        """Before the first plan: for any reading never stored, the entity's last
        good value in the recorder. A battery switched off below its reserve
        reports nothing, and may be off when Home Assistant starts."""
        wanted = {
            "capacity_kwh": CONF_BATTERY_CAPACITY,
            "soc_pct": CONF_BATTERY_SOC,
            "reserve_pct": CONF_BATTERY_RESERVE,
        }
        readings = self.memory.memory.battery
        for key, conf in wanted.items():
            entity_id = self.config_entry.data.get(conf)
            if key in readings or not entity_id:
                continue
            states = await get_instance(self.hass).async_add_executor_job(
                get_last_state_changes, self.hass, 50, entity_id
            )
            for state in reversed(states.get(entity_id, [])):
                value = _number(state)
                if value is None or value < 0 or (key == "capacity_kwh" and value == 0):
                    continue
                if key == "capacity_kwh":
                    unit = (
                        state.attributes.get("unit_of_measurement")
                        or UnitOfEnergy.KILO_WATT_HOUR
                    )
                    try:
                        value = EnergyConverter.convert(
                            value, unit, UnitOfEnergy.KILO_WATT_HOUR
                        )
                    except Exception:
                        continue
                readings[key] = value
                break

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

    def _entity_price(self, key: str = CONF_PRICE_ENTITY) -> float | None:
        entity_id = self.config_entry.options.get(key)
        state = self.hass.states.get(entity_id) if entity_id else None
        value = _number(state)
        if value is None or state is None or value < 0:
            return None
        unit = str(state.attributes.get("unit_of_measurement") or "").lower()
        # Cents per kWh (ct/kWh, c/kWh, cent/kWh), else a currency per kWh.
        return value / 100 if unit.startswith(("ct", "c/", "cent")) else value

    @property
    def price_source(self) -> str:
        """Where the price comes from: the entity while it has a value, else the
        options once saved, else the default."""
        if self._entity_price() is not None:
            return "entity"
        if CONF_PRICE in self.config_entry.options:
            return "fixed"
        return "default"

    @property
    def price(self) -> float:
        """What a kWh bought costs now, in currency per kWh.

        A price entity is read as it stands now and applied to every hour ahead;
        nothing is timed to cheap hours.
        """
        from_entity = self._entity_price()
        if from_entity is not None:
            return from_entity
        return float(self.config_entry.options.get(CONF_PRICE, DEFAULT_PRICE_CT)) / 100

    @property
    def feed_in_source(self) -> str:
        """Where the feed-in tariff comes from, as price_source."""
        if self._entity_price(CONF_FEED_IN_ENTITY) is not None:
            return "entity"
        if CONF_FEED_IN in self.config_entry.options:
            return "fixed"
        return "default"

    @property
    def feed_in(self) -> float:
        """What a kWh exported earns, in currency per kWh: the feed-in entity while
        it has a value, else the option."""
        from_entity = self._entity_price(CONF_FEED_IN_ENTITY)
        if from_entity is not None:
            return from_entity
        return (
            float(self.config_entry.options.get(CONF_FEED_IN, DEFAULT_FEED_IN_CT)) / 100
        )

    def export_cap_kw(self) -> float | None:
        """The feed-in cap in kW, while the entity gives one; None means
        exports are not capped, or the cap is not known."""
        entity_id = self.config_entry.options.get(CONF_FEED_IN_CAP)
        kw = power_kw(self.hass.states.get(entity_id)) if entity_id else None
        return kw if kw is not None and kw > 0 else None

    @property
    def exports_positive(self) -> bool:
        """Whether the grid power reads exports as positive."""
        return not self.config_entry.options.get(CONF_IMPORT_POSITIVE, True)

    def _limit_option(self, floor: float) -> float:
        option = self._option(CONF_MAX_CHARGE, DEFAULT_MAX_CHARGE_PCT)
        return option if floor < option <= 100 else 100.0

    def _limit_entity(self, floor: float) -> float | None:
        """The entity's limit while it gives a sensible value: above the floor,
        at most 100."""
        value = _number(self._state(CONF_CHARGE_LIMIT))
        return value if value is not None and floor < value <= 100 else None

    def charge_limit_now(self, floor: float) -> float:
        """The level charging stops at right now: the entity, else the option,
        else 100."""
        value = self._limit_entity(floor)
        return value if value is not None else self._limit_option(floor)

    def charge_limit(self, floor: float) -> float:
        """The level the battery is meant to reach: the highest the entity has
        been over the last day, so a limit lowered for a few hours doesn't
        count as the day's. A limit kept lower for a whole day is the limit.
        While the entity has no value, the option, else 100."""
        value = self._limit_entity(floor)
        if value is None:
            return self._limit_option(floor)
        now = dt_util.utcnow()
        seen = [(t, v) for t, v in self.limits_seen if now - t <= LIMIT_WINDOW]
        if not seen or seen[-1][1] != value:
            seen.append((now, value))
        self.limits_seen = seen
        return max(v for _, v in seen if floor < v <= 100)

    async def async_seen_limits_from_recorder(self) -> None:
        """Before the first plan: the charge limit's values over the last day,
        so a restart while it is lowered doesn't take the lowered value as the
        day's."""
        entity_id = self.config_entry.data.get(CONF_CHARGE_LIMIT)
        if not entity_id:
            return
        now = dt_util.utcnow()
        changes = await get_instance(self.hass).async_add_executor_job(
            state_changes_during_period, self.hass, now - LIMIT_WINDOW, now, entity_id
        )
        self.limits_seen = [
            (max(state.last_changed, now - LIMIT_WINDOW), value)
            for state in changes.get(entity_id, [])
            if (value := _number(state)) is not None
        ]

    def battery(self) -> Battery:
        # Nothing changes while a battery is switched off, so its last readings stand.
        soc = _number(self._state(CONF_BATTERY_SOC))
        soc = (
            self._keep_reading("soc_pct", soc)
            if soc is not None
            else self._kept_reading("soc_pct")
        )
        if soc is None:
            raise UpdateFailed("The battery's state of charge is not available")
        capacity = self._capacity()
        if not capacity:
            raise UpdateFailed("The battery's capacity is not available")
        reserve = _number(self._state(CONF_BATTERY_RESERVE))
        reserve = (
            self._keep_reading("reserve_pct", reserve)
            if reserve is not None
            else self._kept_reading("reserve_pct")
        ) or 0.0
        floor = max(reserve, self._option(CONF_MIN_RESERVE, DEFAULT_MIN_RESERVE_PCT))
        if soc < SLEEPING_READING_PCT < floor:
            # Some inverters report 0 % while the battery sleeps at its reserve;
            # it cannot be below a floor it never discharges past.
            soc = floor
        return Battery(
            capacity_kwh=capacity,
            soc_pct=soc,
            floor_pct=floor,
            ceiling_pct=self.charge_limit(floor),
            max_power_kw=self._option(CONF_MAX_POWER, DEFAULT_MAX_POWER_KW),
            round_trip=self._option(CONF_ROUND_TRIP, DEFAULT_ROUND_TRIP_PCT) / 100,
        )

    def _house_counter_kwh(self) -> float | None:
        state = self._state(CONF_HOUSE_ENERGY)
        value = _number(state)
        if value is None or state is None:
            return None
        unit = (
            state.attributes.get("unit_of_measurement") or UnitOfEnergy.KILO_WATT_HOUR
        )
        try:
            return float(
                EnergyConverter.convert(value, unit, UnitOfEnergy.KILO_WATT_HOUR)
            )
        except Exception:
            return None

    def _solar_counter_kwh(self) -> float | None:
        entity_id = self.config_entry.options.get(CONF_SOLAR_ENERGY)
        state = self.hass.states.get(entity_id) if entity_id else None
        value = _number(state)
        if value is None or state is None:
            return None
        unit = (
            state.attributes.get("unit_of_measurement") or UnitOfEnergy.KILO_WATT_HOUR
        )
        try:
            return float(
                EnergyConverter.convert(value, unit, UnitOfEnergy.KILO_WATT_HOUR)
            )
        except Exception:
            return None

    @property
    def array(self) -> Array:
        """The array as Solcast's sites describe it, or its size set by hand."""
        kwp = self.config_entry.options.get(CONF_ARRAY_KWP)
        return array_of(self.hass, float(kwp) if kwp else None)

    def forecast(self) -> PvForecast:
        """Solcast, with this roof's correction once it has earned its place."""
        return self.correction.apply(self.raw_forecast(), self.tz)

    def raw_forecast(self) -> PvForecast:
        return PvForecast.from_solcast(
            *(
                state.attributes.get("detailedForecast") if state else None
                for state in (
                    self._state(CONF_SOLCAST_TODAY),
                    self._state(CONF_SOLCAST_TOMORROW),
                )
            )
        )
