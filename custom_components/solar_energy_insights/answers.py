"""What the plan said, and what happened: the morning's and the 21:00 answers,
what changed recently, this morning's call, last night's, and the evening's
use so far."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import (
    UnitOfEnergy,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_LOOKBACK,
    CONF_SOLAR_ENERGY,
    DEFAULT_LOOKBACK_MIN,
    MORNING_UNTIL_HOUR,
    NIGHT_ANSWER_HOUR,
    SHORT_TERM_DAYS,
)
from .core import correction, live
from .core.backtest import (
    Calibration,
    Evening,
    Score,
)
from .core.changes import Answers, Change, detect, keep
from .core.correction import Correction
from .core.forecast import PvForecast, Slot
from .core.live import FullScore, NightAnswer, NightScore
from .core.nights import (
    HourlyUse,
    hourly_use,
    plausible,
)
from .core.plan import Plan, PlanInputs
from .readings import ReadingsMixin

_LOGGER = logging.getLogger(__name__)


class AnswersMixin(ReadingsMixin):
    """The coordinator's record of its answers and their outcomes."""

    use: HourlyUse
    calibration: Calibration | None
    evenings: list[Evening]
    live_nights: NightScore | None
    live_full: FullScore | None
    last_inputs: PlanInputs | None
    evening_used_kwh: float | None
    calls: list[live.Call]
    _recent_answers: list[Answers]
    changes: dict[str, Change]

    def _note_solar_day(self, p: Plan) -> None:
        """Keep the solar counter's reading at the start of each day."""
        counter = self._solar_counter_kwh()
        if counter is None:
            return
        today = dt_util.as_local(p.now).date().isoformat()
        kept = self.memory.memory.solar_day
        if kept.get("day") != today or counter < kept.get("counter", 0):
            self.memory.memory.solar_day = {
                "day": today,
                "counter": counter,
                "at": p.now.isoformat(),
            }
            self.memory.async_save_later()

    def this_morning(self, p: Plan) -> dict[str, Any] | None:
        """In the day: what this morning's plan said about the battery being
        full, and the sun so far against the morning forecast for those hours."""
        if not p.sun_up:
            return None
        local = dt_util.as_local(p.now)
        day = local.date().isoformat()
        said = self.memory.memory.days.get(day)
        if said is None:
            return None
        made: float | None = None
        kept = self.memory.memory.solar_day
        counter = self._solar_counter_kwh()
        # Only a reading from before sunrise counts the whole day's sun.
        if (
            counter is not None
            and kept.get("day") == day
            and datetime.fromisoformat(kept["at"]) <= p.sunrise - timedelta(days=1)
        ):
            made = round(counter - float(kept["counter"]), 2)
        slots = self.memory.memory.forecasts.get(day) or []
        expected = sum(
            float(p50) / 2
            for start, p50, _, _ in slots
            if datetime.fromisoformat(start) + timedelta(minutes=30) <= p.now
        )
        return {
            "said_full_at": said.get("full_at"),
            "said_at": said.get("at"),
            "solar_kwh": made,
            "forecast_kwh": round(expected, 2) if slots else None,
        }

    def last_night(self) -> dict[str, Any] | None:
        """What the plan said at 21:00 last night, what happened, and how many
        of the recent calls were right: from the same record as the live score."""
        yesterday = dt_util.as_local(dt_util.utcnow()).date() - timedelta(days=1)
        if not self.calls or self.calls[-1].day != yesterday:
            return None
        last, recent = self.calls[-1], self.calls[-10:]
        return {
            "said_spare_kwh": round(last.said_spare_kwh, 1),
            "said_lasts": last.said_lasts,
            "spare_kwh": round(last.spare_kwh, 1),
            "lasted": last.lasted,
            "right_calls": sum(c.right for c in recent),
            "calls": len(recent),
        }

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
        window = timedelta(minutes=self._option(CONF_LOOKBACK, DEFAULT_LOOKBACK_MIN))
        self._recent_answers = keep(self._recent_answers, now, window)
        self.changes = detect(self._recent_answers, self._usual_kw(p.now), window)

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
        if i.sun_up:
            return None
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

    def _remember(self, p: Plan) -> None:
        """Keep the morning forecast and "full at", and the 21:00 night answer."""
        memory = self.memory.memory
        local = dt_util.as_local(p.now)
        day = local.date().isoformat()
        changed = False
        daytime = p.sun_up
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
        self.calls = live.calls(answers, spare, lasted)
        self.live_nights = live.score_nights(self.calls)
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
