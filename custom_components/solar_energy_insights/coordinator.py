"""Recomputes the plan when the battery or the forecast moves.

The only state kept is in memory and comes back from the recorder after a
restart: the house's hourly use over the last nights, re-read once an hour.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfEnergy,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.sun import get_astral_event_date, get_astral_event_next
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .achievements import Achievements
from .answers import AnswersMixin
from .const import (
    BACKTEST_EVENINGS,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_RESERVE,
    CONF_BATTERY_SOC,
    CONF_CHARGE_LIMIT,
    CONF_GRID_POWER,
    CONF_HOUSE_ENERGY,
    CONF_PRICE_ENTITY,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    DOMAIN,
    EVENT_FREE_POWER,
    HISTORY_MINUTE,
    LIVE_COOLDOWN_S,
    LIVE_INTERVAL_MIN,
)
from .core import live
from .core.backtest import (
    EVENING,
    Calibration,
    Evening,
    calibrate,
    clean_lows,
)
from .core.battery import Rate
from .core.changes import Answers, Change
from .core.correction import Correction
from .core.live import FullScore, NightScore
from .core.nights import (
    HourlyUse,
    hourly_use,
)
from .core.plan import HEAVY_SHARE, Plan, PlanInputs, plan
from .core.snapshot import to_snapshot
from .forecast_checkpoints import Checkpoints
from .memory import MemoryStore

_LOGGER = logging.getLogger(__name__)

type InsightsConfigEntry = ConfigEntry[InsightsCoordinator]


class InsightsCoordinator(AnswersMixin, DataUpdateCoordinator[Plan]):
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
        self.checkpoints = Checkpoints(self)
        # The inputs behind the plan shown, for an exact replay.
        self.last_inputs: PlanInputs | None = None
        # The last capacity reported, for a battery that reports none while asleep.
        # Readings taken from memory because the battery reports none right now.
        self.remembered: set[str] = set()
        # What the house has used since this evening's sunset, kWh; None by day.
        self.evening_used_kwh: float | None = None
        # Each night's 21:00 call and what happened: the live score's record.
        self.calls: list[live.Call] = []
        # The last half hour of answers, and what changed in it.
        self._answers: list[Answers] = []
        self.changes: dict[str, Change] = {}

    # ── Reading ──

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
        try:
            await self.checkpoints.async_update()
        except Exception:
            _LOGGER.exception("Could not judge the forecast checkpoints")

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

    async def _async_update_data(self) -> Plan:
        await self.async_load_use()
        previous: Plan | None = self.data
        self.last_inputs = self.inputs()
        result = plan(self.last_inputs)
        self._remember(result)
        self.evening_used_kwh = self._evening_used(self.last_inputs)
        self._note_answers(result)
        self._note_solar_day(result)
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
