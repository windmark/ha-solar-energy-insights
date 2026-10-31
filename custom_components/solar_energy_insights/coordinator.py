"""Recomputes the plan when the battery or the forecast moves.

The only state kept is in memory and comes back from the recorder after a
restart: the house's hourly use over the last nights, re-read once an hour.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

import numpy as np
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
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

from .achievements import Achievements
from .array import array_of
from .const import (
    BACKTEST_EVENINGS,
    CONF_ARRAY_KWP,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_HEALTH,
    CONF_BATTERY_RESERVE,
    CONF_BATTERY_SOC,
    CONF_CHARGE_LIMIT,
    CONF_CHARGE_POWER_LIMIT,
    CONF_DISCHARGE_POWER_LIMIT,
    CONF_FEED_IN,
    CONF_GRID_POWER,
    CONF_HOUSE_ENERGY,
    CONF_IMPORT_POSITIVE,
    CONF_MAX_CHARGE,
    CONF_MAX_POWER,
    CONF_MIN_RESERVE,
    CONF_NIGHTS,
    CONF_PRESENCE,
    CONF_PRICE,
    CONF_PRICE_ENTITY,
    CONF_ROUND_TRIP,
    CONF_SOLAR_ENERGY,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    CONF_TEMPERATURE,
    DEFAULT_FEED_IN_CT,
    DEFAULT_MAX_CHARGE_PCT,
    DEFAULT_MAX_POWER_KW,
    DEFAULT_MIN_RESERVE_PCT,
    DEFAULT_NIGHTS,
    DEFAULT_PRICE_CT,
    DEFAULT_ROUND_TRIP_PCT,
    DOMAIN,
    EVENT_FREE_POWER,
    FEED_IN_HELPER,
    HISTORY_MINUTE,
    LIVE_COOLDOWN_S,
    LIVE_INTERVAL_MIN,
    MORNING_UNTIL_HOUR,
    NIGHT_ANSWER_HOUR,
    PRICE_HELPER,
    SHORT_TERM_DAYS,
)
from .core import correction, live
from .core.array import Array
from .core.backtest import (
    EVENING,
    NO_FEATURES,
    Calibration,
    Evening,
    Features,
    Score,
    Weighting,
    calibrate,
    clean_lows,
    weights_for,
)
from .core.battery import Battery, Rate
from .core.correction import Correction
from .core.forecast import PvForecast, Slot
from .core.live import FullScore, NightAnswer, NightScore
from .core.nights import (
    HourlySeries,
    HourlyUse,
    hourly_means,
    hourly_use,
    past_days,
    plausible,
)
from .core.plan import HEAVY_SHARE, Plan, PlanInputs, plan
from .core.snapshot import to_snapshot
from .memory import MemoryStore

# A state of charge below this, with a floor above it, is a sleeping battery.
SLEEPING_READING_PCT = 0.5
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
        self.calibration: Calibration | None = None
        self.evenings: list[Evening] = []
        # Hours of battery history that held impossible readings, now corrected.
        self.corrected_readings = 0
        # What the house counter does not see, learned from real nights.
        self.standby_kw = 0.0
        # Where a heavy night sits among the replayed nights, calibrated.
        self.heavy_share = HEAVY_SHARE
        # Optional hourly series the weighting may use.
        self.features = NO_FEATURES
        # What the recorder cannot give back: morning forecasts, own answers.
        self.memory = MemoryStore(hass, entry.entry_id)
        self.correction = Correction.none()
        self.live_nights: NightScore | None = None
        self.live_full: FullScore | None = None
        self.achievements = Achievements(self)
        # The inputs behind the plan shown, for an exact replay.
        self.last_inputs: PlanInputs | None = None
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
        """Where the price comes from: the entity while it has a value, else the
        options once saved, else the dashboard's helper, else the default."""
        if self._entity_price() is not None:
            return "entity"
        if CONF_PRICE in self.config_entry.options:
            return "fixed"
        if _number(self.hass.states.get(PRICE_HELPER)) is not None:
            return "helper"
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
        return self._cents(CONF_PRICE, PRICE_HELPER, DEFAULT_PRICE_CT) / 100

    @property
    def feed_in(self) -> float:
        """What a kWh exported earns, in currency per kWh."""
        return self._cents(CONF_FEED_IN, FEED_IN_HELPER, DEFAULT_FEED_IN_CT) / 100

    def _cents(self, option: str, helper: str, default: float) -> float:
        """A price in cents: the option once saved, else the helper the dashboard
        already uses, else the default."""
        if option in self.config_entry.options:
            return float(self.config_entry.options[option])
        value = _number(self.hass.states.get(helper))
        return value if value is not None and value >= 0 else default

    def charge_limit(self, floor: float) -> float:
        """The level charging stops at: the entity while it gives a sensible
        value (above the floor, at most 100), else the option, else 100."""
        value = _number(self._state(CONF_CHARGE_LIMIT))
        if value is not None and floor < value <= 100:
            return value
        option = self._option(CONF_MAX_CHARGE, DEFAULT_MAX_CHARGE_PCT)
        return option if floor < option <= 100 else 100.0

    def battery(self) -> Battery:
        soc = _number(self._state(CONF_BATTERY_SOC))
        if soc is None:
            raise UpdateFailed("The battery's state of charge is not available")
        capacity = self._capacity()
        if not capacity:
            raise UpdateFailed("The battery's capacity is not available")
        reserve = _number(self._state(CONF_BATTERY_RESERVE)) or 0.0
        floor = max(reserve, self._option(CONF_MIN_RESERVE, DEFAULT_MIN_RESERVE_PCT))
        if soc < SLEEPING_READING_PCT < floor:
            # Some inverters report 0 % while the battery sleeps at its reserve;
            # it cannot be below a floor it never discharges past.
            soc = floor
        fixed = self._option(CONF_MAX_POWER, DEFAULT_MAX_POWER_KW)
        charge_kw = self._power_limit(CONF_CHARGE_POWER_LIMIT)
        discharge_kw = self._power_limit(CONF_DISCHARGE_POWER_LIMIT)
        return Battery(
            capacity_kwh=capacity * self.health,
            soc_pct=soc,
            floor_pct=floor,
            ceiling_pct=self.charge_limit(floor),
            max_power_kw=charge_kw if charge_kw is not None else fixed,
            round_trip=self._option(CONF_ROUND_TRIP, DEFAULT_ROUND_TRIP_PCT) / 100,
            max_discharge_kw=discharge_kw if discharge_kw is not None else None,
        )

    def entities(self, key: str) -> list[str]:
        """An option holding one entity or several (one per battery pack)."""
        value = self.config_entry.options.get(key)
        if not value:
            return []
        return [value] if isinstance(value, str) else list(value)

    @property
    def health(self) -> float:
        """The share of nominal capacity the battery still holds: the average
        of its packs' state of health, when reported."""
        readings = [
            v
            for entity_id in self.entities(CONF_BATTERY_HEALTH)
            if (v := _number(self.hass.states.get(entity_id))) is not None
            and 50 <= v <= 100
        ]
        return sum(readings) / len(readings) / 100 if readings else 1.0

    def _power_limit(self, key: str) -> float | None:
        """A live power limit from the battery, in kW, while it reports one."""
        entity_id = self.config_entry.options.get(key)
        state = self.hass.states.get(entity_id) if entity_id else None
        value = _number(state)
        if value is None or value <= 0 or state is None:
            return None
        unit = state.attributes.get("unit_of_measurement") or UnitOfPower.WATT
        try:
            kw = PowerConverter.convert(value, unit, UnitOfPower.KILO_WATT)
        except Exception:
            return None
        return kw if 0.1 <= kw <= 100 else None

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

    def inputs(
        self, extra: Rate | None = None, now: datetime | None = None
    ) -> PlanInputs:
        now = now or dt_util.utcnow()
        days = past_days(self.use, self.tz, self.nights)
        weights = weights_for(
            self.weighting,
            self.use,
            self.tz,
            now,
            days,
            self.features,
            self._partial_kw(now),
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
            standby_kw=self.standby_kw,
            heavy_share=self.heavy_share,
        )

    # ── History ──

    async def async_refresh_history(self) -> None:
        """Re-read the history now, whether or not a new hour has been recorded."""
        self._use_until = None
        await self.async_load_use()

    async def async_load_use(self) -> None:
        """Re-read the house's hourly use once the recorder has a new hour.

        If the recorder cannot answer (it can be busy), the history already read
        is kept and it is tried again on the next update: one failed read must
        not take every answer away.
        """
        now = dt_util.utcnow()
        hour = now.replace(minute=0, second=0, microsecond=0)
        if now.minute < HISTORY_MINUTE:
            hour -= timedelta(hours=1)
        if self._use_until == hour:
            return
        try:
            await self._async_read_history(now, hour)
        except Exception as err:
            if not self.use:
                raise UpdateFailed(f"Could not read history: {err}") from err
            _LOGGER.warning("Could not read history, keeping what was read: %s", err)
            return
        self._use_until = hour

    async def _async_read_history(self, now: datetime, hour: datetime) -> None:
        house = self.config_entry.data[CONF_HOUSE_ENERGY]
        soc = self.config_entry.data[CONF_BATTERY_SOC]
        options = self.config_entry.options
        temperature = options.get(CONF_TEMPERATURE) or None
        presence = options.get(CONF_PRESENCE) or None
        start = hour - timedelta(days=self.nights + BACKTEST_EVENINGS + 2)
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            hour,
            {e for e in (house, soc, temperature, presence) if e},
            "hour",
            {
                "energy": UnitOfEnergy.KILO_WATT_HOUR,
                "temperature": UnitOfTemperature.CELSIUS,
            },
            {"change", "state", "min", "mean", "max"},
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
        soc_hours = {
            dt_util.utc_from_timestamp(row["start"]): (row.get("min"), row.get("mean"))
            for row in rows.get(soc, [])
        }
        self.features = Features(
            temperature=self._means(rows, temperature),
            presence=self._means(rows, presence),
        )
        await self._async_calibrate(soc_hours, now)
        await self._async_score_live(rows, soc, now)
        try:
            await self.achievements.async_update()
        except Exception:
            _LOGGER.exception("Could not update achievements")

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

    def _means(
        self, rows: dict[str, Any], entity_id: str | None
    ) -> HourlySeries | None:
        if not entity_id:
            return None
        means = {
            dt_util.utc_from_timestamp(row["start"]): mean
            for row in rows.get(entity_id, [])
            if (mean := row.get("mean")) is not None
        }
        return hourly_means(means, self.tz) or None

    async def _async_calibrate(
        self,
        soc_hours: dict[datetime, tuple[float | None, float | None]],
        now: datetime,
    ) -> None:
        """Learn from recent evenings: the weighting, and the hidden load."""
        try:
            battery = self.battery()
        except UpdateFailed:
            return
        lows, self.corrected_readings = clean_lows(soc_hours, battery.floor_pct)
        self.evenings = self._evenings(lows, now)
        # A few thousand short simulations: off the event loop.
        self.calibration = await self.hass.async_add_executor_job(
            calibrate,
            self.use,
            self.tz,
            self.evenings,
            battery,
            self.nights,
            self.weighting,
            self.features,
        )
        self.weighting = self.calibration.weighting
        self.standby_kw = self.calibration.standby_kw
        self.heavy_share = self.calibration.heavy_share
        if self.memory.memory.weighting != str(self.weighting):
            self.memory.memory.weighting = str(self.weighting)
            self.memory.async_save_later()

    # ── Remembering and scoring the plan's own answers ──

    def restore(self) -> None:
        """Pick up what was remembered before a restart."""
        saved = self.memory.memory.weighting
        if saved in {str(w) for w in Weighting}:
            self.weighting = Weighting(saved)

    def _remember(self, p: Plan) -> None:
        """Keep the morning forecast and "full at", and the 21:00 night answer."""
        memory = self.memory.memory
        local = dt_util.as_local(p.now)
        day = local.date().isoformat()
        changed = False
        daytime = p.sunset < p.sunrise
        if daytime and local.hour < MORNING_UNTIL_HOUR and day not in memory.days:
            memory.forecasts[day] = [
                [s.start.isoformat(), s.p50, s.p10, s.p90]
                for s in self.raw_forecast().slots
                if dt_util.as_local(s.start).date() == local.date()
            ]
            full_at = p.charge.full_at
            memory.days[day] = {
                "at": p.now.isoformat(),
                "full_at": full_at.isoformat() if full_at else None,
            }
            changed = True
        if (
            local.hour >= NIGHT_ANSWER_HOUR
            and day not in memory.nights
            and p.night is not None
        ):
            memory.nights[day] = {
                "at": p.now.isoformat(),
                "spare_kwh": p.night.typical.spare_kwh,
                "lasts": p.night.typical.lasts,
                "outlook": str(p.night.outlook),
            }
            changed = True
        if changed:
            memory.prune(local.date())
            self.memory.async_save_later()

    async def _async_score_live(
        self, rows: dict[str, Any], soc: str, now: datetime
    ) -> None:
        """Score what the plan said against what happened, and learn this roof's
        correction to Solcast."""
        memory = self.memory.memory
        today = dt_util.as_local(now).date()
        try:
            battery = self.battery()
        except UpdateFailed:
            return
        # The night answers, against the same evenings the backtest uses.
        floor, cap = battery.floor_pct, battery.capacity_kwh
        spare = {
            e.day: max(e.lowest_soc - floor, 0.0) / 100 * cap for e in self.evenings
        }
        lasted = {e.day: e.lowest_soc > floor + 1.0 for e in self.evenings}
        answers = {
            date.fromisoformat(day): NightAnswer(a["spare_kwh"], a["lasts"])
            for day, a in memory.nights.items()
        }
        self.live_nights = live.score_nights(answers, spare, lasted)
        # "Full at", to five minutes where the recorder still has it.
        highest = {
            dt_util.utc_from_timestamp(row["start"]): value
            for row in rows.get(soc, [])
            if (value := row.get("max")) is not None
        }
        fine = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            now - timedelta(days=SHORT_TERM_DAYS),
            now,
            {soc},
            "5minute",
            None,
            {"max"},
        )
        fine_days = set()
        for row in fine.get(soc, []):
            if (value := row.get("max")) is not None:
                start = dt_util.utc_from_timestamp(row["start"])
                fine_days.add(dt_util.as_local(start).date())
        highest = {
            t: v
            for t, v in highest.items()
            if dt_util.as_local(t).date() not in fine_days
        } | {
            dt_util.utc_from_timestamp(row["start"]): value
            for row in fine.get(soc, [])
            if (value := row.get("max")) is not None
        }
        # Full is reaching the charge limit, which may be below 100 %.
        full_pct = battery.ceiling_pct - 1.0
        actual_full = {
            day: t
            for day, t in live.full_times(highest, self.tz, full_pct).items()
            if day < today
        }
        predicted_full = {
            date.fromisoformat(day): (
                datetime.fromisoformat(a["full_at"]) if a.get("full_at") else None
            )
            for day, a in memory.days.items()
        }
        self.live_full = live.score_full(predicted_full, actual_full)
        # This roof's correction, from the saved mornings and what the panels made.
        solar = self.config_entry.options.get(CONF_SOLAR_ENERGY)
        if not solar:
            self.correction = Correction.none()
            return
        if not memory.forecasts:
            self.correction = Correction.none()
            return
        oldest = date.fromisoformat(min(memory.forecasts))
        made = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            datetime.combine(oldest, datetime.min.time(), self.tz) - timedelta(hours=1),
            now,
            {solar},
            "hour",
            {"energy": UnitOfEnergy.KILO_WATT_HOUR},
            {"change"},
        )
        hourly = hourly_use(
            # The same counter jumps the achievements guard against.
            plausible(
                {
                    dt_util.utc_from_timestamp(row["start"]): change
                    for row in made.get(solar, [])
                    if (change := row.get("change")) is not None
                }
            ),
            self.tz,
            # A large array can make more than a house uses in an hour.
            max_kwh=None,
        )
        forecasts, actual = {}, {}
        for day_key, slots in memory.forecasts.items():
            day = date.fromisoformat(day_key)
            if day >= today or not all((day, h) in hourly for h in range(24)):
                continue
            saved = PvForecast(
                Slot(datetime.fromisoformat(t), p50, p10, p90)
                for t, p50, p10, p90 in slots
            )
            forecasts[day] = np.array(saved.hourly(day, self.tz))
            actual[day] = np.array([hourly[(day, h)] for h in range(24)])
        self.correction = correction.learn(forecasts, actual)

    @property
    def score(self) -> Score | None:
        """How the night answer did on recent evenings, as it is used now."""
        return self.calibration.score if self.calibration else None

    # ── Updating ──

    async def _async_update_data(self) -> Plan:
        await self.async_load_use()
        previous: Plan | None = self.data
        self.last_inputs = self.inputs()
        result = plan(self.last_inputs)
        self._remember(result)
        if result.free_power and not (previous is not None and previous.free_power):
            self.hass.bus.async_fire(EVENT_FREE_POWER, {})
        return result

    def snapshot(self) -> dict[str, Any]:
        """Everything the plan shown was made from, to replay it offline."""
        if self.last_inputs is None:
            return {"error": "No plan yet"}
        return to_snapshot(self.last_inputs, self.evenings)

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
                CONF_CHARGE_LIMIT,
                CONF_BATTERY_RESERVE,
                CONF_GRID_POWER,
                CONF_SOLCAST_TODAY,
                CONF_SOLCAST_TOMORROW,
            )
            if (entity_id := self.config_entry.data.get(key))
        ]
        if price_entity := self.config_entry.options.get(CONF_PRICE_ENTITY):
            entity_ids.append(price_entity)
        entity_ids.extend(
            entity_id
            for key in (CONF_CHARGE_POWER_LIMIT, CONF_DISCHARGE_POWER_LIMIT)
            if (entity_id := self.config_entry.options.get(key))
        )
        entity_ids.extend(self.entities(CONF_BATTERY_HEALTH))
        return async_track_state_change_event(
            self.hass, entity_ids, self._async_changed
        )

    @callback
    def _async_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_request_refresh())
