"""Learning from history: hourly, daily, at sunrise and at fixed forecast times.

Every job reads completed periods from the recorder rather than sampling live
states at a deadline, so a restart or a slow midnight only delays it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import state_changes_during_period
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import (
    BACKFILL_DAYS,
    CATCH_UP_HOURS,
    CONF_BATTERY_CHARGE_ENERGY,
    CONF_BATTERY_DISCHARGE_ENERGY,
    CONF_BATTERY_SOC,
    CONF_COOK_THRESHOLD,
    CONF_GRID_EXPORT_ENERGY,
    CONF_GRID_IMPORT_ENERGY,
    CONF_HOUSE_ENERGY,
    CONF_HOUSE_POWER,
    CONF_MAX_POWER,
    CONF_SOLAR_ENERGY,
    CONF_SOLAR_POWER,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    CONF_UPGRADE_COST,
    CONF_UPGRADE_KWH,
    DAILY_TIME,
    DEFAULT_COOK_THRESHOLD_KW,
    DEFAULT_MAX_POWER_KW,
    DEFAULT_UPGRADE_COST_EUR,
    DEFAULT_UPGRADE_KWH,
    HOURLY_MINUTE,
    KEEP_DAYS,
    RUNNING_STATES,
    SNAPSHOT_DAY_AHEAD,
    SNAPSHOT_FINAL,
    SNAPSHOT_MIDDAY,
    SNAPSHOT_MORNING,
)
from .core.bookkeeping import (
    DayRecord,
    DayTotals,
    Forecasts,
    Night,
    score,
    sizing_verdict,
)
from .core.forecast import PvForecast
from .core.load import COOK_WINDOW, LoadModel, cycle_day, detect_cooking
from .core.upgrade import Hour, ceiling_per_year, evaluate
from .history import Series, async_series, extreme, total
from .store import EveningCycle, InsightsData

if TYPE_CHECKING:
    from .coordinator import InsightsCoordinator

_LOGGER = logging.getLogger(__name__)

HOUR = timedelta(hours=1)
# Short-term (5-minute) statistics are kept this long by default.
SHORT_TERM_DAYS = 9
DAY_RETRIES = 4
RETRY_AFTER = timedelta(minutes=15)
# After sunrise, wait this long before judging the night.
NIGHT_GRACE = timedelta(minutes=30)
FULL_SOC = 99.5


def _hour_floor(t: datetime) -> datetime:
    return dt_util.as_utc(t).replace(minute=0, second=0, microsecond=0)


def _local_midnight(day: date) -> datetime:
    return dt_util.as_utc(
        datetime.combine(day, time(), dt_util.get_default_time_zone())
    )


def _hours(start: datetime, end: datetime) -> list[datetime]:
    out, t = [], start
    while t < end:
        out.append(t)
        t += HOUR
    return out


class Learner:
    """Runs the scheduled jobs for one coordinator."""

    def __init__(self, coordinator: InsightsCoordinator) -> None:
        self.c = coordinator
        self.hass: HomeAssistant = coordinator.hass
        self._unsubs: list[Callable[[], None]] = []
        self._day_retries: dict[date, int] = {}

    @property
    def data(self) -> InsightsData:
        return self.c.data_store

    # ── Scheduling ──

    @callback
    def async_start(self) -> None:
        track = self._unsubs.append
        track(
            async_track_time_change(
                self.hass, self._async_hourly, minute=HOURLY_MINUTE, second=0
            )
        )
        track(
            async_track_time_change(
                self.hass,
                self._async_daily,
                hour=DAILY_TIME[0],
                minute=DAILY_TIME[1],
                second=0,
            )
        )
        for when, handler in (
            (SNAPSHOT_MORNING, self._async_snapshot_morning),
            (SNAPSHOT_MIDDAY, self._async_snapshot_midday),
            (SNAPSHOT_DAY_AHEAD, self._async_snapshot_day_ahead),
            (SNAPSHOT_FINAL, self._async_snapshot_final),
        ):
            track(
                async_track_time_change(
                    self.hass, handler, hour=when[0], minute=when[1], second=0
                )
            )

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    async def _async_hourly(self, now: datetime) -> None:
        await self.async_learn_hours(now)
        await self.async_watch_cooking(now)
        await self.async_judge_night(now)
        self._finish()

    async def _async_daily(self, now: datetime) -> None:
        yesterday = dt_util.as_local(now).date() - timedelta(days=1)
        await self.async_close_days(yesterday)
        await self.async_value_upgrade()
        self._finish()

    def _finish(self) -> None:
        self.c.store.async_schedule_save()
        self.c.async_update_listeners()

    async def async_catch_up(self) -> None:
        """After a restart, or the first setup: fill in what was missed."""
        now = dt_util.utcnow()
        first_time = not self.data.records
        if self.data.last_hour is None:
            # Learn the load from the past weeks rather than start cold.
            self.data.last_hour = (
                _hour_floor(now) - timedelta(days=BACKFILL_DAYS)
            ).isoformat()
        await self.async_learn_hours(
            now, limit=BACKFILL_DAYS * 24 if first_time else CATCH_UP_HOURS
        )
        yesterday = dt_util.as_local(now).date() - timedelta(days=1)
        await self.async_close_days(
            yesterday, backfill=BACKFILL_DAYS if first_time else 7
        )
        await self.async_judge_night(now)
        if first_time or not self.data.upgrade:
            await self.async_value_upgrade()
        self._finish()

    # ── Hourly: the house load ──

    async def _unmetered_hours(self, start: datetime, end: datetime) -> set[datetime]:
        """Hours an appliance without a meter ran: the load was not the house's."""
        hours: set[datetime] = set()
        for appliance in self.c.appliances:
            if appliance.metered or not appliance.state_entity:
                continue
            states: dict[str, list[State]] = await get_instance(
                self.hass
            ).async_add_executor_job(
                state_changes_during_period,
                self.hass,
                start,
                end,
                appliance.state_entity,
                True,
            )
            changes = states.get(appliance.state_entity, [])
            for n, state in enumerate(changes):
                if state.state.lower().rsplit(".", 1)[-1] not in RUNNING_STATES:
                    continue
                since = max(state.last_changed, start)
                until = changes[n + 1].last_changed if n + 1 < len(changes) else end
                t = _hour_floor(since)
                while t < until:
                    hours.add(t)
                    t += HOUR
        return hours

    async def async_learn_hours(
        self, now: datetime, limit: int = CATCH_UP_HOURS
    ) -> None:
        end = _hour_floor(now)
        start = end - HOUR * limit
        if self.data.last_hour:
            start = max(
                start, dt_util.as_utc(datetime.fromisoformat(self.data.last_hour))
            )
        if start >= end:
            return
        house_id = self.c.entity(CONF_HOUSE_ENERGY)
        metered = [a.energy_entity for a in self.c.appliances if a.energy_entity]
        changes = await async_series(
            self.hass, [house_id or "", *metered], start, end, "hour", "change"
        )
        skipped = await self._unmetered_hours(start, end)
        house = changes.get(house_id or "", {})
        tz = self.c.tz
        for t in _hours(start, end):
            used = house.get(t)
            value: float | None = None
            if used is not None and t not in skipped:
                appliances = sum(changes.get(e, {}).get(t, 0.0) for e in metered)
                value = max(used - appliances, 0.0)
                self.data.load.observe_hour(t, value, tz)
            await self._add_to_cycle(t, value)
        self.data.last_hour = end.isoformat()

    async def _add_to_cycle(self, t: datetime, value: float | None) -> None:
        tz = self.c.tz
        day = cycle_day(t, tz).isoformat()
        cycle = self.data.cycle
        if cycle.day != day:
            if cycle.day is not None and cycle.day < day:
                await self._close_cycle(cycle)
            self.data.cycle = cycle = EveningCycle(day)
        if value is None:
            return
        hour = dt_util.as_local(t).hour
        known = cycle.hours[hour]
        # A clock hour seen twice (the autumn change) holds both.
        cycle.hours[hour] = round(value + (known or 0.0), 4)

    async def _close_cycle(self, cycle: EveningCycle) -> None:
        assert cycle.day is not None
        day = date.fromisoformat(cycle.day)
        cooked = cycle.day in self.data.cooked or await self._cooked_on(day)
        if cooked is None:
            # Unknown kind of evening: learning it as either would blur both.
            return
        self.data.load.observe_evening(cycle.hours, cooked)

    async def _cooked_on(self, day: date) -> bool | None:
        """Whether the evening of *day* had cooking, from 5-minute statistics."""
        if dt_util.as_local(dt_util.utcnow()).date() - day > timedelta(
            days=SHORT_TERM_DAYS
        ):
            return None
        start = _local_midnight(day) + timedelta(hours=COOK_WINDOW[0])
        end = _local_midnight(day) + timedelta(hours=COOK_WINDOW[1])
        house_id = self.c.entity(CONF_HOUSE_POWER)
        powers = [a.power_entity for a in self.c.appliances if a.power_entity]
        means = await async_series(
            self.hass, [house_id or "", *powers], start, end, "5minute", "mean"
        )
        house = means.get(house_id or "")
        if not house:
            return None
        samples = [
            (t, kw - sum(means.get(p, {}).get(t, 0.0) for p in powers))
            for t, kw in house.items()
        ]
        threshold = float(self.c.option(CONF_COOK_THRESHOLD, DEFAULT_COOK_THRESHOLD_KW))
        cooked = detect_cooking(samples, self.c.tz, threshold)
        if cooked:
            self.data.mark_cooked(day)
        return cooked

    async def async_watch_cooking(self, now: datetime) -> None:
        """During the evening, catch cooking the live listener missed."""
        local = dt_util.as_local(now)
        if COOK_WINDOW[0] < local.hour <= COOK_WINDOW[1]:
            await self._cooked_on(cycle_day(now, self.c.tz))

    # ── Daily: the record ──

    async def async_close_days(self, day: date, backfill: int = 0) -> None:
        """Write the record for *day*, and for missing days before it."""
        for back in range(backfill, -1, -1):
            target = day - timedelta(days=back)
            if back and self.data.record(target):
                continue
            complete = await self.async_close_day(target)
            if complete is False and back == 0:
                self._retry_day(target)

    def _retry_day(self, day: date) -> None:
        tries = self._day_retries.get(day, 0)
        if tries >= DAY_RETRIES:
            return
        self._day_retries[day] = tries + 1

        async def again(_now: datetime) -> None:
            await self.async_close_days(day)
            self._finish()

        self._unsubs.append(async_call_later(self.hass, RETRY_AFTER, again))

    async def async_close_day(self, day: date) -> bool | None:
        """Build the day's record from statistics.

        Returns whether the day was complete, or None when nothing was recorded.
        """
        start, end = _local_midnight(day), _local_midnight(day + timedelta(days=1))
        keys = {
            "solar": CONF_SOLAR_ENERGY,
            "house": CONF_HOUSE_ENERGY,
            "imp": CONF_GRID_IMPORT_ENERGY,
            "exp": CONF_GRID_EXPORT_ENERGY,
            "charged": CONF_BATTERY_CHARGE_ENERGY,
            "discharged": CONF_BATTERY_DISCHARGE_ENERGY,
        }
        ids = {name: self.c.entity(key) or "" for name, key in keys.items()}
        changes = await async_series(
            self.hass, ids.values(), start, end, "hour", "change"
        )
        house = changes.get(ids["house"], {})
        if not house:
            return None
        complete = end - HOUR in house
        totals = DayTotals(
            **{name: total(changes.get(e), start, end) for name, e in ids.items()}
        )
        soc_id = self.c.entity(CONF_BATTERY_SOC) or ""
        solar_id = self.c.entity(CONF_SOLAR_POWER) or ""
        maxes = await async_series(
            self.hass, [soc_id, solar_id], start, end, "hour", "max"
        )
        soc_max = extreme(maxes.get(soc_id), start, end, highest=True)
        full_at = await self._first_full(soc_id, start, end, maxes.get(soc_id))
        snap = self.data.snapshots.get("today") or {}
        same_day = snap.get("day") == day.isoformat()
        forecasts = Forecasts(
            day_ahead=snap.get("day_ahead") if same_day else None,
            morning=snap.get("morning") if same_day else None,
            midday=snap.get("midday") if same_day else None,
            final=snap.get("final") if same_day else None,
            morning_hourly=snap.get("morning_hourly") if same_day else None,
        )
        previous = self.data.record(day)
        record = DayRecord(
            day=day,
            totals=totals,
            soc_max=soc_max,
            full_at=full_at,
            peak_kw=extreme(maxes.get(solar_id), start, end, highest=True),
            forecasts=forecasts if same_day or previous is None else previous.forecasts,
            score=score(
                totals,
                self.c.capacity_kwh,
                self.c.floor_pct,
                forecasts.morning or forecasts.final,
            ),
            night=previous.night if previous else None,
        )
        if same_day and forecasts.morning_hourly and complete:
            self._learn_bias(
                day, forecasts.morning_hourly, changes.get(ids["solar"], {})
            )
        self.data.add_record(record)
        return complete

    def _learn_bias(self, day: date, forecast: list[float], solar: Series) -> None:
        actual = [0.0] * 24
        for t, kwh in solar.items():
            local = dt_util.as_local(t)
            if local.date() == day:
                actual[local.hour] += max(kwh, 0.0)
        self.data.pv_bias.observe_day(day.month, forecast, actual)

    async def _first_full(
        self, soc_id: str, start: datetime, end: datetime, hourly_max: Series | None
    ) -> datetime | None:
        """When the battery first got full, to five minutes where that is kept."""
        fine = await async_series(self.hass, [soc_id], start, end, "5minute", "max")
        for series in (fine.get(soc_id), hourly_max):
            for t in sorted(series or {}):
                if (series or {})[t] >= FULL_SOC:
                    return t
        return None

    # ── At sunrise: the night ──

    async def async_judge_night(self, now: datetime) -> None:
        """Judge last night once the sun is up: grid use and whether the battery was
        the right size for the day before it."""
        today = dt_util.as_local(now).date()
        sunrise = get_astral_event_date(self.hass, "sunrise", today)
        evening = today - timedelta(days=1)
        sunset = get_astral_event_date(self.hass, "sunset", evening)
        record = self.data.record(evening)
        if (
            sunrise is None
            or sunset is None
            or record is None
            or record.night is not None
            or now < sunrise + NIGHT_GRACE
        ):
            return
        soc_id = self.c.entity(CONF_BATTERY_SOC) or ""
        imp_id = self.c.entity(CONF_GRID_IMPORT_ENERGY) or ""
        exp_id = self.c.entity(CONF_GRID_EXPORT_ENERGY) or ""
        full_at = record.full_at
        start = min(full_at or sunset, sunset)
        # Five minutes where the recorder still has them, else whole hours.
        period: Literal["5minute", "hour"] = "5minute"
        changes = await async_series(
            self.hass, [imp_id, exp_id], start, sunrise, period, "change"
        )
        if not changes.get(imp_id):
            period, start, sunset = "hour", _hour_floor(start), _hour_floor(sunset)
            if full_at:
                full_at = _hour_floor(full_at)
            changes = await async_series(
                self.hass, [imp_id, exp_id], start, sunrise, period, "change"
            )
        min_after_full = None
        if full_at:
            mins = await async_series(
                self.hass, [soc_id], full_at, sunrise, period, "min"
            )
            min_after_full = extreme(mins.get(soc_id), full_at, sunrise, highest=False)
        night = Night(
            grid_kwh=total(changes.get(imp_id), sunset, sunrise),
            min_soc_after_full=min_after_full,
            export_after_full_kwh=total(changes.get(exp_id), full_at, sunset)
            if full_at
            else None,
            verdict=sizing_verdict(record.soc_max, min_after_full, self.c.floor_pct),
        )
        self.data.add_record(
            DayRecord(
                record.day,
                record.totals,
                record.soc_max,
                record.full_at,
                record.peak_kw,
                record.forecasts,
                record.score,
                night,
            )
        )

    # ── Forecast snapshots ──

    def _solcast_total(self, key: str) -> float | None:
        state = self.c.state(self.c.entity(key))
        try:
            return round(float(state.state), 2) if state else None
        except ValueError:
            return None

    def _today_snapshot(self, now: datetime) -> dict[str, Any]:
        day = dt_util.as_local(now).date().isoformat()
        snap = self.data.snapshots.get("today")
        if not snap or snap.get("day") != day:
            ahead = self.data.snapshots.get("next") or {}
            snap = {
                "day": day,
                "day_ahead": ahead.get("total") if ahead.get("day") == day else None,
            }
            self.data.snapshots["today"] = snap
        return snap

    async def _async_snapshot_morning(self, now: datetime) -> None:
        snap = self._today_snapshot(now)
        snap["morning"] = self._solcast_total(CONF_SOLCAST_TODAY)
        state = self.c.state(self.c.entity(CONF_SOLCAST_TODAY))
        raw = PvForecast.from_solcast(
            state.attributes.get("detailedForecast") if state else None
        )
        if len(raw):
            snap["morning_hourly"] = [
                round(v, 3) for v in raw.hourly(dt_util.as_local(now).date(), self.c.tz)
            ]
        self._finish()

    async def _async_snapshot_midday(self, now: datetime) -> None:
        self._today_snapshot(now)["midday"] = self._solcast_total(CONF_SOLCAST_TODAY)
        self._finish()

    async def _async_snapshot_day_ahead(self, now: datetime) -> None:
        tomorrow = dt_util.as_local(now).date() + timedelta(days=1)
        self.data.snapshots["next"] = {
            "day": tomorrow.isoformat(),
            "total": self._solcast_total(CONF_SOLCAST_TOMORROW),
        }
        self._finish()

    async def _async_snapshot_final(self, now: datetime) -> None:
        self._today_snapshot(now)["final"] = self._solcast_total(CONF_SOLCAST_TODAY)
        self._finish()

    # ── The bigger battery ──

    async def async_hours(self) -> list[Hour]:
        """Every recorded hour of import and export, up to the kept history."""
        end = _hour_floor(dt_util.utcnow())
        start = end - timedelta(days=KEEP_DAYS)
        imp_id = self.c.entity(CONF_GRID_IMPORT_ENERGY) or ""
        exp_id = self.c.entity(CONF_GRID_EXPORT_ENERGY) or ""
        changes = await async_series(
            self.hass, [imp_id, exp_id], start, end, "hour", "change"
        )
        imp, exp = changes.get(imp_id, {}), changes.get(exp_id, {})
        return [
            Hour(t, max(imp.get(t, 0.0), 0.0), max(exp.get(t, 0.0), 0.0))
            for t in sorted(set(imp) | set(exp))
        ]

    async def async_upgrade(
        self, sizes: list[float], hours: list[Hour] | None = None
    ) -> dict[str, Any]:
        hours = hours if hours is not None else await self.async_hours()
        price, feed_in = self.c.price_eur_kwh, self.c.feed_in_eur_kwh
        round_trip, tz = self.c.round_trip, self.c.tz
        max_kw = float(self.c.option(CONF_MAX_POWER, DEFAULT_MAX_POWER_KW))
        results = await self.hass.async_add_executor_job(
            lambda: [
                evaluate(
                    hours,
                    size,
                    price_eur_kwh=price,
                    feed_in_eur_kwh=feed_in,
                    round_trip=round_trip,
                    max_kw=max_kw,
                    tz=tz,
                )
                for size in sizes
            ]
        )
        ceiling = ceiling_per_year(
            hours,
            price_eur_kwh=price,
            feed_in_eur_kwh=feed_in,
            round_trip=round_trip,
            tz=tz,
        )
        return {
            "since": hours[0].start.isoformat() if hours else None,
            "hours": len(hours),
            "ceiling_per_year_eur": round(ceiling, 2),
            "sizes": [
                {
                    "kwh": r.extra_kwh,
                    "per_year_eur": r.per_year_eur,
                    "value_eur": r.value_eur,
                    "kwh_shifted": r.kwh_shifted,
                    "months_measured": r.months_measured,
                    "months": r.months,
                }
                for r in results
            ],
        }

    async def async_value_upgrade(self) -> None:
        size = float(self.c.option(CONF_UPGRADE_KWH, DEFAULT_UPGRADE_KWH))
        cost = float(self.c.option(CONF_UPGRADE_COST, DEFAULT_UPGRADE_COST_EUR))
        result = await self.async_upgrade([size])
        chosen = result["sizes"][0]
        per_year = chosen["per_year_eur"]
        self.data.upgrade = {
            "kwh": size,
            "cost_eur": cost,
            "per_year_eur": per_year,
            "payback_years": round(cost / per_year, 1) if per_year > 0 else None,
            "months_measured": chosen["months_measured"],
            "ceiling_per_year_eur": result["ceiling_per_year_eur"],
            "since": result["since"],
        }


def reset_load(data: InsightsData) -> None:
    """Forget the learned load, to learn it again from history."""
    data.load = LoadModel()
    data.cycle = EveningCycle()
    data.last_hour = None
