"""What the recorder cannot give back, kept in .storage.

- Solcast's forecast as it stood each morning: the recorder does not keep the
  half-hourly detail, and without it the day side cannot be scored, nor a
  correction for this roof learned.
- The plan's own answers when they were given: the night at 21:00, "full at"
  in the morning, so live predictions are scored, not only replays.

Everything else is still read from the recorder. Losing this file loses only
history for the day side; the integration carries on without it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
KEEP_DAYS = 90
SAVE_DELAY_S = 60


@dataclass
class Memory:
    # The battery's last good readings (capacity_kwh, soc_pct, reserve_pct), for
    # when it is switched off below its reserve and its entities go unavailable.
    battery: dict[str, float] = field(default_factory=dict)
    # The house energy counter at this evening's sunset: {"day", "counter", "at"},
    # for how much the evening has used so far.
    evening: dict[str, Any] = field(default_factory=dict)
    # The solar energy counter at the start of the day: {"day", "counter", "at"},
    # for what the panels have made so far.
    solar_day: dict[str, Any] = field(default_factory=dict)
    # Day → [[start, p50, p10, p90], …], Solcast's half hours that morning.
    forecasts: dict[str, list[list[Any]]] = field(default_factory=dict)
    # Day → {"at": when, "full_at": time or None}, said in the morning.
    days: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Day → {"at": when, "spare_kwh": …, "lasts": …, "outlook": …}, said at 21:00.
    nights: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Achievements: each closed day's facts, the quests set, and what has been
    # announced (so a restart or a rebuild does not announce it again).
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    quests: dict[str, dict[str, Any]] = field(default_factory=dict)
    announced: dict[str, Any] | None = None
    # Which way the stored facts were built; an older one is rebuilt.
    facts_version: int = 0

    def prune(self, today: date) -> None:
        oldest = (today - timedelta(days=KEEP_DAYS)).isoformat()
        for book in (self.forecasts, self.days, self.nights):
            for key in [k for k in book if k < oldest]:
                del book[key]

    def as_dict(self) -> dict[str, Any]:
        return {
            "forecasts": self.forecasts,
            "days": self.days,
            "nights": self.nights,
            "facts": self.facts,
            "quests": self.quests,
            "announced": self.announced,
            "facts_version": self.facts_version,
            "battery": self.battery,
            "evening": self.evening,
            "solar_day": self.solar_day,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Memory:
        if not data:
            return cls()
        return cls(
            forecasts=dict(data.get("forecasts") or {}),
            days=dict(data.get("days") or {}),
            nights=dict(data.get("nights") or {}),
            facts=dict(data.get("facts") or {}),
            quests=dict(data.get("quests") or {}),
            announced=data.get("announced"),
            facts_version=int(data.get("facts_version") or 0),
            battery={k: float(v) for k, v in (data.get("battery") or {}).items()},
            evening=dict(data.get("evening") or {}),
            solar_day=dict(data.get("solar_day") or {}),
        )


class MemoryStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.memory = Memory()

    async def async_load(self) -> Memory:
        self.memory = Memory.from_dict(await self._store.async_load())
        return self.memory

    def async_save_later(self) -> None:
        self._store.async_delay_save(self.memory.as_dict, SAVE_DELAY_S)

    async def async_save(self) -> None:
        await self._store.async_save(self.memory.as_dict())

    async def async_remove(self) -> None:
        await self._store.async_remove()
