"""Achievements in Home Assistant: day facts from the recorder, the trophy room,
quests, and announcements. The game itself is in core/achievements."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    BACKFILL_DAYS,
    CONF_ACHIEVEMENTS,
    CONF_BATTERY_SOC,
    CONF_GRID_EXPORT_ENERGY,
    CONF_GRID_IMPORT_ENERGY,
    CONF_HOUSE_ENERGY,
    CONF_SOLAR_ENERGY,
    CONF_SOLAR_POWER,
    EVENT_ACHIEVEMENT,
    QUEST_HOUR,
    REBUILD_DAYS,
    SHORT_TERM_DAYS,
)
from .core.achievements import quests as quest_rules
from .core.achievements.facts import DayFacts, Series, build
from .core.achievements.profile import profile_of
from .core.achievements.quests import Quest
from .core.achievements.trophies import Trophies, as_dict, compute
from .core.backtest import EVENING, Evening, clean_lows
from .core.forecast import PvForecast
from .core.nights import hourly_means, hourly_use, plausible

if TYPE_CHECKING:
    from .coordinator import InsightsCoordinator

_LOGGER = logging.getLogger(__name__)

# Raised whenever stored facts would come out differently from the same history:
# they are then rebuilt from the recorder, and nothing is announced for it.
# 2: implausible counter jumps dropped. 3: no battery cycle counts.
FACTS_VERSION = 3


class Achievements:
    """Keeps the day facts current and the trophy room computed."""

    def __init__(self, coordinator: InsightsCoordinator) -> None:
        self.c = coordinator
        self.trophies: Trophies | None = None
        self.live: DayFacts | None = None
        self._built_for: date | None = None
        # What the entities and the WebSocket command show, as of the last update.
        self.view: dict[str, Any] | None = None
        self._computed_key: tuple[Any, ...] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.c.config_entry.options.get(CONF_ACHIEVEMENTS, False))

    @property
    def memory(self) -> Any:
        return self.c.memory.memory

    def _entity(self, key: str) -> str | None:
        value = self.c.config_entry.data.get(key) or self.c.config_entry.options.get(
            key
        )
        return str(value) if value else None

    # ── Reading the recorder ──

    async def _series(
        self, start: datetime, end: datetime
    ) -> tuple[Series, dict[datetime, float]]:
        tz = self.c.tz
        energy = {
            key: self._entity(key)
            for key in (
                CONF_HOUSE_ENERGY,
                CONF_SOLAR_ENERGY,
                CONF_GRID_IMPORT_ENERGY,
                CONF_GRID_EXPORT_ENERGY,
            )
        }
        soc = self._entity(CONF_BATTERY_SOC)
        power = self._entity(CONF_SOLAR_POWER)
        ids = {e for e in (*energy.values(), soc, power) if e}
        recorder = get_instance(self.c.hass)
        rows = await recorder.async_add_executor_job(
            statistics_during_period,
            self.c.hass,
            start,
            end,
            ids,
            "hour",
            {"energy": UnitOfEnergy.KILO_WATT_HOUR, "power": UnitOfPower.KILO_WATT},
            {"change", "min", "max", "mean"},
        )

        def changes(
            entity_id: str | None, limit: float | None
        ) -> dict[tuple[date, int], float] | None:
            if not entity_id or entity_id not in rows:
                return None
            raw = {
                dt_util.utc_from_timestamp(r["start"]): v
                for r in rows[entity_id]
                if (v := r.get("change")) is not None
            }
            return hourly_use(plausible(raw), tz, max_kwh=limit) or None

        def field(entity_id: str | None, name: str) -> dict[datetime, float]:
            if not entity_id:
                return {}
            return {
                dt_util.utc_from_timestamp(r["start"]): float(v)  # type: ignore[arg-type]
                for r in rows.get(entity_id, [])
                if (v := r.get(name)) is not None
            }

        floor = self.c.battery().floor_pct
        lows, _ = clean_lows(
            {
                dt_util.utc_from_timestamp(r["start"]): (r.get("min"), r.get("mean"))
                for r in rows.get(soc or "", [])
            },
            floor,
        )
        fine_rows = await recorder.async_add_executor_job(
            statistics_during_period,
            self.c.hass,
            max(start, end - timedelta(days=SHORT_TERM_DAYS)),
            end,
            {soc} if soc else set(),
            "5minute",
            None,
            {"max"},
        )
        fine = {
            dt_util.utc_from_timestamp(r["start"]): v
            for r in fine_rows.get(soc or "", [])
            if (v := r.get("max")) is not None
        }
        series = Series(
            house=changes(energy[CONF_HOUSE_ENERGY], 15.0) or {},
            solar=changes(energy[CONF_SOLAR_ENERGY], None),
            imported=changes(energy[CONF_GRID_IMPORT_ENERGY], None),
            exported=changes(energy[CONF_GRID_EXPORT_ENERGY], None),
            soc_max=hourly_means(field(soc, "max"), tz) or None,
            soc_min=hourly_means(lows, tz) or None,
            solar_peak=hourly_means(field(power, "max"), tz) or None,
            soc_fine=fine or None,
        )
        return series, lows

    def _evening(self, day: date, lows: dict[datetime, float]) -> Evening | None:
        tz = self.c.tz
        start = datetime.combine(day, EVENING, tz)
        sunrise = get_astral_event_date(self.c.hass, "sunrise", day + timedelta(days=1))
        if sunrise is None or sunrise > dt_util.utcnow():
            return None
        before = lows.get(start - timedelta(hours=1))
        night = [v for t, v in lows.items() if start <= t < sunrise]
        if before is None or not night:
            return None
        return Evening(day, before, sunrise, min(night))

    def _forecast(self, day: date) -> float | None:
        slots = self.memory.forecasts.get(day.isoformat())
        if not slots:
            return None
        return round(sum(float(p50) for _, p50, _, _ in slots) / 2, 2)

    def _said(self, day: date) -> dict[str, Any]:
        said: dict[str, Any] = {}
        if (morning := self.memory.days.get(day.isoformat())) is not None:
            said["full_at"] = morning.get("full_at")
        if (night := self.memory.nights.get(day.isoformat())) is not None and night.get(
            "outlook"
        ):
            said["outlook"] = night["outlook"]
        return said

    # ── Keeping up ──

    async def async_update(self) -> None:
        """Called with each history read: closes days, sets and judges quests,
        computes the room and announces what is new."""
        if not self.enabled:
            return
        now = dt_util.utcnow()
        local = dt_util.as_local(now)
        today = local.date()
        try:
            battery = self.c.battery()
        except UpdateFailed:
            return
        memory = self.memory
        if memory.facts_version != FACTS_VERSION:
            memory.facts.clear()
            memory.announced = None
            memory.facts_version = FACTS_VERSION
            self._built_for = None
            self._computed_key = None
        if self._built_for != today or not memory.facts:
            first = date.fromisoformat(min(memory.facts)) if memory.facts else None
            start_day = (
                today - timedelta(days=BACKFILL_DAYS)
                if first is None
                else today - timedelta(days=REBUILD_DAYS + 1)
            )
            start = datetime.combine(start_day, datetime.min.time(), self.c.tz)
            series, lows = await self._series(start, now)
            day = start_day
            while day < today:
                closed = build(
                    day,
                    self.c.tz,
                    series,
                    ceiling_pct=battery.ceiling_pct,
                    floor_pct=battery.floor_pct,
                    capacity_kwh=battery.capacity_kwh,
                    evening=self._evening(day, lows),
                    forecast_kwh=self._forecast(day),
                    said=self._said(day),
                )
                if closed is not None:
                    memory.facts[day.isoformat()] = closed.as_dict()
                day += timedelta(days=1)
            self._built_for = today
            self.c.memory.async_save_later()
        else:
            start = datetime.combine(today, datetime.min.time(), self.c.tz)
            series, _ = await self._series(start, now)
        self.live = build(
            today,
            self.c.tz,
            series,
            ceiling_pct=battery.ceiling_pct,
            floor_pct=battery.floor_pct,
            capacity_kwh=battery.capacity_kwh,
            min_hours=1,
        )
        history = [DayFacts.from_dict(v) for _, v in sorted(memory.facts.items())]
        array = self.c.array
        profile = profile_of(
            history,
            battery.capacity_kwh,
            self.c.hass.config.latitude,
            kwp=array.kwp,
            relative_yield=array.relative_yield,
        )
        self._set_quests(history, profile, local)
        quests = []
        for raw in sorted(memory.quests.values(), key=lambda q: (q["day"], q["id"])):
            quest = Quest.from_dict({k: v for k, v in raw.items() if k != "set"})
            if quest.result is None:
                result = quest_rules.judge(quest, history, self.c.tz)
                if result is not None:
                    quest = replace(quest, result=result)
                    memory.quests[quest.id] = {**raw, "result": result}
            quests.append(quest)
        key = (
            len(memory.facts),
            max(memory.facts, default=""),
            tuple((q.id, q.result) for q in quests),
            battery.floor_pct,
        )
        if key != self._computed_key or self.trophies is None:
            trophies = await self.c.hass.async_add_executor_job(
                compute, history, profile, self.c.tz, today, quests, battery.floor_pct
            )
            self.trophies = trophies
            self._computed_key = key
            self._announce(trophies)
        self.view = self.as_dict()

    def _set_quests(self, facts: list[DayFacts], profile: Any, local: datetime) -> None:
        memory = self.memory
        today = local.date()
        if local.weekday() == 0:
            weekly = quest_rules.this_week(facts, profile, today)
            if weekly is not None and weekly.id not in memory.quests:
                memory.quests[weekly.id] = weekly.as_dict()
        if local.hour < QUEST_HOUR:
            return
        tomorrow = today + timedelta(days=1)
        if any(
            q.get("day") in (today.isoformat(), tomorrow.isoformat())
            and q.get("kind") != "grid_free_nights"
            and q.get("set") == today.isoformat()
            for q in memory.quests.values()
        ):
            return
        plan = self.c.plan
        outlook = str(plan.night.outlook) if plan and plan.night else None
        quest = quest_rules.next_day(
            facts,
            profile,
            self.c.tz,
            tomorrow,
            self._tomorrow_forecast(tomorrow),
            outlook,
        )
        if quest is not None:
            memory.quests[quest.id] = {**quest.as_dict(), "set": today.isoformat()}
            self.c.memory.async_save_later()

    def _tomorrow_forecast(self, day: date) -> float | None:
        forecast: PvForecast = self.c.raw_forecast()
        slots = [s for s in forecast.slots if dt_util.as_local(s.start).date() == day]
        return round(sum(s.p50 for s in slots) / 2, 2) if slots else None

    # ── Announcing ──

    def _announce(self, t: Trophies) -> None:
        memory = self.memory
        state = {
            "level": t.level.level,
            "tiers": {b.badge.id: b.tier for b in t.badges},
            "records": {r.id: r.day.isoformat() for r in t.records},
            "quests": sorted(q.id for q in t.quests if q.result),
            "months": [m.month for m in t.months],
        }
        before = memory.announced
        memory.announced = state
        self.c.memory.async_save_later()
        if before is None:
            # The first replay of history: everything is already earned.
            return
        fire = self.c.hass.bus.async_fire
        if state["level"] > before.get("level", 0):
            fire(
                EVENT_ACHIEVEMENT,
                {
                    "kind": "level",
                    "title": f"Level {t.level.level}: {t.level.name}",
                    "message": f"Solar level {t.level.level} reached: {t.level.name}.",
                    "icon": "mdi:star-shooting",
                },
            )
        for b in t.badges:
            if b.tier > before.get("tiers", {}).get(b.badge.id, 0):
                tier = ("bronze", "silver", "gold")[b.tier - 1]
                fire(
                    EVENT_ACHIEVEMENT,
                    {
                        "kind": "badge",
                        "badge": b.badge.id,
                        "tier": tier,
                        "title": f"{b.badge.name}: {tier}",
                        "message": (
                            f"New badge, {b.badge.name} ({tier}): "
                            f"{b.badge.description}."
                        ),
                        "icon": b.badge.icon,
                    },
                )
        for r in t.records:
            if before.get("records", {}).get(r.id) != r.day.isoformat():
                fire(
                    EVENT_ACHIEVEMENT,
                    {
                        "kind": "record",
                        "record": r.id,
                        "title": f"New record: {r.name}",
                        "message": f"New record, {r.name}: {r.value:g} {r.unit}.",
                        "icon": r.icon,
                    },
                )
        for q in t.quests:
            if q.result and q.id not in before.get("quests", []):
                fire(
                    EVENT_ACHIEVEMENT,
                    {
                        "kind": "quest",
                        "quest": q.id,
                        "title": f"Quest done: {q.title}",
                        "message": f"Quest done, {q.title}: +{q.reward} XP.",
                        "icon": "mdi:flag-checkered",
                    },
                )
        closed = [m for m in t.months[:-1] if m.month not in before.get("months", [])]
        for m in closed:
            fire(
                EVENT_ACHIEVEMENT,
                {
                    "kind": "recap",
                    "month": m.month,
                    "title": f"{m.month} in review",
                    "message": (
                        f"{m.month}: average score {m.score:g}, "
                        f"{m.nights_lasted} of {m.nights} "
                        f"nights without the grid"
                        + (
                            f", {m.kept_kwh:g} kWh of sun kept at home, worth "
                            f"{m.kept_kwh * self.c.price:.2f} "
                            f"{self.c.hass.config.currency}"
                            if m.kept_kwh is not None
                            else ""
                        )
                        + "."
                    ),
                    "icon": "mdi:calendar-star",
                },
            )

    # ── For entities and the WebSocket API ──

    def as_dict(self) -> dict[str, Any] | None:
        if self.trophies is None:
            return None
        return as_dict(
            self.trophies,
            dt_util.as_local(dt_util.utcnow()).date(),
            self.live,
            self.c.battery().floor_pct,
            (self.c.price, self.c.feed_in),
        )
