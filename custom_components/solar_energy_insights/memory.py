"""What the recorder cannot give back, kept in .storage.

- Solcast's forecast as it stood each morning: the recorder does not keep the
  half-hourly detail, and without it the day side cannot be scored, nor a
  correction for this roof learned.
- The plan's own answers when they were given: the night at 21:00, "full at"
  in the morning, so live predictions are scored, not only replays.
- The weighting in use, so "switch only for a clear gain" survives a restart.

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
    weighting: str | None = None
    # Day → [[start, p50, p10, p90], …], Solcast's half hours that morning.
    forecasts: dict[str, list[list[Any]]] = field(default_factory=dict)
    # Day → {"at": when, "full_at": time or None}, said in the morning.
    days: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Day → {"at": when, "spare_kwh": …, "lasts": …}, said at 21:00.
    nights: dict[str, dict[str, Any]] = field(default_factory=dict)

    def prune(self, today: date) -> None:
        oldest = (today - timedelta(days=KEEP_DAYS)).isoformat()
        for book in (self.forecasts, self.days, self.nights):
            for key in [k for k in book if k < oldest]:
                del book[key]

    def as_dict(self) -> dict[str, Any]:
        return {
            "weighting": self.weighting,
            "forecasts": self.forecasts,
            "days": self.days,
            "nights": self.nights,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Memory:
        if not data:
            return cls()
        return cls(
            weighting=data.get("weighting"),
            forecasts=dict(data.get("forecasts") or {}),
            days=dict(data.get("days") or {}),
            nights=dict(data.get("nights") or {}),
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
