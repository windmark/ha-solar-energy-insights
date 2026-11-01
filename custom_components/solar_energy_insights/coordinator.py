"""Recomputes the plan when the battery or the forecast moves.

The only state kept is in memory and comes back from the recorder after a
restart: the house's hourly use over the last nights, re-read once an hour.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

import numpy as np
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_last_state_changes
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

from .achievements import Achievements
from .array import array_of
from .const import (
    BACKTEST_EVENINGS,
    CONF_ARRAY_KWP,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_RESERVE,
    CONF_BATTERY_SOC,
    CONF_CHARGE_LIMIT,
    CONF_FEED_IN,
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
    Calibration,
    Evening,
    Score,
    calibrate,
    clean_lows,
)
from .core.battery import Battery, Rate
from .core.changes import Answers, Change, detect, keep
from .core.correction import Correction
from .core.forecast import PvForecast, Slot
from .core.live import FullScore, NightAnswer, NightScore
from .core.nights import (
    HourlyUse,
    hourly_use,
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
        self.calibration: Calibration | None = None
        self.evenings: list[Evening] = []
        # Hours of battery history that held impossible readings, now corrected.
        self.corrected_readings = 0
        # What the house counter does not see, learned from real nights.
        self.standby_kw = 0.0
        # Where a heavy night sits among the replayed nights, calibrated.
        self.heavy_share = HEAVY_SHARE
        # What the recorder cannot give back: morning forecasts, own answers.
        self.memory = MemoryStore(hass, entry.entry_id)
        self.correction = Correction.none()
        self.live_nights: NightScore | None = None
        self.live_full: FullScore | None = None
        self.achievements = Achievements(self)
        # The inputs behind the plan shown, for an exact replay.
        self.last_inputs: PlanInputs | None = None
        # The last capacity reported, for a battery that reports none while asleep.
        # Readings taken from memory because the battery reports none right now.
        self.remembered: set[str] = set()
        # What the house has used since this evening's sunset, kWh; None by day.
        self.evening_used_kwh: float | None = None
        # The last half hour of answers, and what changed in it.
        self._answers: list[Answers] = []
        self.changes: dict[str, Change] = {}

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
        # A battery switched off below its reserve reports nothing: its last
        # readings stand, since nothing changes while it is off.
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

    def _note_answers(self, p: Plan) -> None:
        """Keep the last half hour of answers, and find what changed in it."""
        night = p.night
        now = Answers(
            at=p.now,
            outlook=str(night.outlook) if night else None,
            grid_from=night.typical.reserve_at if night else None,
            spare_kwh=night.typical.spare_kwh if night else None,
            full_at=p.charge.full_at,
            house_kwh=self._house_counter_kwh(),
            forecast_kwh=self._sun_left_today(p.now),
        )
        self._answers = keep(self._answers, now)
        self.changes = detect(self._answers, self._usual_kw(p.now))

    def _sun_left_today(self, now: datetime) -> float | None:
        """What Solcast still expects today, kWh."""
        try:
            forecast = self.forecast()
        except UpdateFailed:
            return None
        end = datetime.combine(
            now.astimezone(self.tz).date() + timedelta(days=1), time(), self.tz
        )
        return round(sum(s.p50 for s in forecast.slots if now <= s.start < end) / 2, 2)

    def _usual_kw(self, now: datetime) -> float | None:
        """The house's usual use in this clock hour, kW, over the recent days."""
        local = now.astimezone(self.tz)
        values = sorted(
            kwh
            for (day, hour), kwh in self.use.items()
            if hour == local.hour and day < local.date()
        )[-self.nights * 2 :]
        return values[len(values) // 2] if values else None

    def _evening_used(self, i: PlanInputs) -> float | None:
        """The house's use since tonight's sunset, from its energy counter.

        The counter's reading at sunset is kept. When it was first read after
        sunset (Home Assistant started in the evening), the recorder's hours
        between sunset and then are added.
        """
        if not i.sunrise < i.sunset:
            return None  # daytime: the sun is up
        night_of = (i.sunset - timedelta(days=1)).astimezone(self.tz).date()
        counter = self._house_counter_kwh()
        if counter is None:
            return None
        evening = self.memory.memory.evening
        if evening.get("day") != night_of.isoformat() or counter < evening.get(
            "counter", 0
        ):
            sunset = i.sunset - timedelta(days=1)
            before = sum(
                kwh
                for (day, hour), kwh in self.use.items()
                if sunset
                <= datetime.combine(day, time(hour), self.tz)
                < i.now - timedelta(hours=1)
            )
            evening = {"day": night_of.isoformat(), "counter": counter - before}
            self.memory.memory.evening = evening
            self.memory.async_save_later()
        return round(max(counter - float(evening["counter"]), 0.0), 2)

    def recent_nights(self) -> dict[str, Any]:
        """What really happened on the recent nights: how many lasted until
        sunrise, from the battery as it was at 21:00, and whether last night did."""
        if self.calibration is None or not self.calibration.score.nights:
            return {}
        nights = sorted(self.calibration.score.nights, key=lambda n: n.day)[
            -self.nights :
        ]
        return {
            "recent_nights": len(nights),
            "recent_nights_lasted": sum(n.actual_lasts for n in nights),
            "last_night_lasted": nights[-1].actual_lasts,
        }

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
        start = hour - timedelta(days=self.nights + BACKTEST_EVENINGS + 2)
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            hour,
            {house, soc},
            "hour",
            {"energy": UnitOfEnergy.KILO_WATT_HOUR},
            {"change", "state", "min", "mean", "max"},
        )
        house_rows = rows.get(house, [])
        changes = {
            dt_util.utc_from_timestamp(row["start"]): change
            for row in house_rows
            if (change := row.get("change")) is not None
        }
        self.use = hourly_use(changes, self.tz)
        soc_hours = {
            dt_util.utc_from_timestamp(row["start"]): (row.get("min"), row.get("mean"))
            for row in rows.get(soc, [])
        }
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

    async def _async_calibrate(
        self,
        soc_hours: dict[datetime, tuple[float | None, float | None]],
        now: datetime,
    ) -> None:
        """Learn from recent evenings: the hidden load and the heavy night."""
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
        )
        self.standby_kw = self.calibration.standby_kw
        self.heavy_share = self.calibration.heavy_share

    # ── Remembering and scoring the plan's own answers ──

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
        self.evening_used_kwh = self._evening_used(self.last_inputs)
        self._note_answers(result)
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
        return async_track_state_change_event(
            self.hass, entity_ids, self._async_changed
        )

    @callback
    def _async_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_request_refresh())
