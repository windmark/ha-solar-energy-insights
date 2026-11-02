"""Forecast checkpoints in Home Assistant: freeze Solcast's forecast at 06:00,
12:00, 18:00 and 21:00, judge each against the solar counter once its day is
over, and keep the scores. The judging is in core/checkpoints.py."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import CONF_SOLAR_ENERGY
from .core.array import clear_day_kwh
from .core.checkpoints import CHECKPOINTS, Judged, Snapshot, judge, scores

if TYPE_CHECKING:
    from collections.abc import Callable

    from .coordinator import InsightsCoordinator

_LOGGER = logging.getLogger(__name__)
SOLCAST = "solcast_solar"
KEEP_DAYS = 400


def solcast_polled_at(hass: HomeAssistant) -> datetime | None:
    """When Solcast last refreshed its forecast ("API Last Polled")."""
    for entry in er.async_get(hass).entities.values():
        if entry.platform == SOLCAST and entry.unique_id.endswith(
            ("last_updated", "lastupdated")
        ):
            state = hass.states.get(entry.entity_id)
            return dt_util.parse_datetime(state.state) if state else None
    return None


class Checkpoints:
    """Freezes the forecast at the checkpoints, and judges and scores it."""

    def __init__(self, c: InsightsCoordinator) -> None:
        self.c = c
        self.scores: dict[str, Any] | None = None

    @property
    def store(self) -> dict[str, dict[str, Any]]:
        return self.c.memory.memory.checkpoints

    @property
    def judged(self) -> dict[str, dict[str, Any]]:
        return self.c.memory.memory.judged

    def follow(self) -> Callable[[], None]:
        """Take a snapshot on the hour at each checkpoint."""
        hours = [hour for _, hour, _, _ in CHECKPOINTS]
        return async_track_time_change(
            self.c.hass, self._at_checkpoint, hour=hours, minute=0, second=0
        )

    @callback
    def _at_checkpoint(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        for name, hour, ahead, _ in CHECKPOINTS:
            if hour == local.hour:
                self.take(name, local.date() + timedelta(days=ahead), now)

    def take(self, name: str, day: date, now: datetime) -> None:
        """Freeze the forecast for *day*, as it stands now."""
        try:
            forecast = self.c.raw_forecast()
        except Exception:
            _LOGGER.debug("No forecast for the %s checkpoint", name)
            return
        slots = tuple(
            (s.start, s.p50, s.p10, s.p90)
            for s in forecast.slots
            if dt_util.as_local(s.start).date() == day
        )
        if not slots:
            return
        snap = Snapshot(name, day, now, solcast_polled_at(self.c.hass), slots)
        self.store[f"{day.isoformat()}|{name}"] = snap.as_dict()
        self.c.memory.async_save_later()
        # Show it now, not at the next plan.
        self.c.async_update_listeners()

    async def async_update(self) -> None:
        """Judge the snapshots whose day is over, and score them all."""
        solar = self.c.config_entry.options.get(CONF_SOLAR_ENERGY)
        today = dt_util.now().date()
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        for book in (self.store, self.judged):
            for key in [k for k in book if k < cutoff]:
                del book[key]
        if solar:
            waiting = [
                Snapshot.from_dict(d)
                for key, d in sorted(self.store.items())
                if d["day"] < today.isoformat() and key not in self.judged
            ]
            if waiting:
                hourly = await self._hourly(
                    str(solar), min(s.day for s in waiting), today
                )
                for snap in waiting:
                    self._judge(snap, hourly)
                self.c.memory.async_save_later()
        rows = [
            Judged(
                j["name"],
                date.fromisoformat(j["day"]),
                j["forecast_kwh"],
                j["low_kwh"],
                j["high_kwh"],
                j["actual_kwh"],
                j["group"],
            )
            for row in self.judged.values()
            for j in row["rows"]
        ]
        self.scores = scores(rows, today)

    def _judge(self, snap: Snapshot, hourly: dict[datetime, float]) -> None:
        tz = dt_util.get_default_time_zone()
        start = datetime.combine(snap.day, datetime.min.time(), tz)
        noon = start + timedelta(hours=12)
        end = start + timedelta(days=1)
        whole = sum(v for t, v in hourly.items() if start <= t < end)
        afternoon = sum(v for t, v in hourly.items() if noon <= t < end)
        array, lon = self.c.array, self.c.hass.config.longitude
        clear_day = clear_day_kwh(array, snap.day, tz, lon)
        rows = []
        if snap.name == "12:00":
            rows.append(
                judge(
                    snap, afternoon, clear_day_kwh(array, snap.day, tz, lon, noon), noon
                )
            )
        else:
            rows.append(judge(snap, whole, clear_day))
        if snap.name == "06:00":
            # The morning's forecast on the afternoon too: does waiting until noon pay?
            row = judge(
                snap, afternoon, clear_day_kwh(array, snap.day, tz, lon, noon), noon
            )
            rows.append(
                Judged(
                    "06:00@noon",
                    *(
                        getattr(row, f)
                        for f in (
                            "day",
                            "forecast_kwh",
                            "low_kwh",
                            "high_kwh",
                            "actual_kwh",
                            "group",
                        )
                    ),
                )
            )
        self.judged[f"{snap.day.isoformat()}|{snap.name}"] = {
            "rows": [
                {
                    "name": r.name,
                    "day": r.day.isoformat(),
                    "forecast_kwh": r.forecast_kwh,
                    "low_kwh": r.low_kwh,
                    "high_kwh": r.high_kwh,
                    "actual_kwh": r.actual_kwh,
                    "group": r.group,
                }
                for r in rows
            ]
        }

    async def _hourly(
        self, solar: str, first: date, today: date
    ) -> dict[datetime, float]:
        """The solar counter's energy per hour, from *first* to the end of yesterday."""
        tz = dt_util.get_default_time_zone()
        rows = await get_instance(self.c.hass).async_add_executor_job(
            statistics_during_period,
            self.c.hass,
            datetime.combine(first, datetime.min.time(), tz),
            datetime.combine(today, datetime.min.time(), tz),
            {solar},
            "hour",
            None,
            {"change"},
        )
        return {
            dt_util.utc_from_timestamp(r["start"]): max(r.get("change") or 0.0, 0.0)
            for r in rows.get(solar, [])
        }

    def view(self) -> dict[str, Any]:
        """Today's and tomorrow's frozen forecasts, and the scores."""
        today = dt_util.now().date()
        frozen: dict[str, dict[str, Any]] = {"today": {}, "tomorrow": {}}
        for d in self.store.values():
            when = {
                today.isoformat(): "today",
                (today + timedelta(days=1)).isoformat(): "tomorrow",
            }.get(d["day"])
            if when is None:
                continue
            snap = Snapshot.from_dict(d)
            noon = None
            if snap.name == "12:00":
                noon = datetime.combine(
                    snap.day, datetime.min.time(), dt_util.get_default_time_zone()
                ) + timedelta(hours=12)
            likely, low, high = snap.kwh(noon)
            frozen[when][snap.name] = {
                "forecast_kwh": likely,
                "low_kwh": low,
                "high_kwh": high,
                "taken_at": d["taken_at"],
                "polled_at": d["polled_at"],
            }
        return {
            **frozen,
            "scores": self.scores,
            "needs_solar_counter": not self.c.config_entry.options.get(
                CONF_SOLAR_ENERGY
            ),
        }
