"""What the integration learns and remembers, in .storage, not in attributes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, KEEP_DAYS
from .core.appliances import ApplianceProfile, RunTracker
from .core.bookkeeping import DayRecord
from .core.forecast import PvBias
from .core.load import LoadModel

STORAGE_VERSION = 1
SAVE_DELAY_S = 30


@dataclass(slots=True)
class EveningCycle:
    """The house load of one noon-to-noon cycle, by clock hour, while it runs."""

    day: str | None = None
    hours: list[float | None] = field(default_factory=lambda: [None] * 24)


@dataclass(slots=True)
class ApplianceState:
    tracker: RunTracker = field(default_factory=RunTracker)
    runs: list[tuple[float, float]] = field(default_factory=list)


@dataclass(slots=True)
class InsightsData:
    load: LoadModel = field(default_factory=LoadModel)
    pv_bias: PvBias = field(default_factory=PvBias.empty)
    records: dict[str, DayRecord] = field(default_factory=dict)
    # Solcast frozen at fixed times: {"today": {...}, "next": {...}}.
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    cycle: EveningCycle = field(default_factory=EveningCycle)
    appliances: dict[str, ApplianceState] = field(default_factory=dict)
    last_hour: str | None = None
    upgrade: dict[str, Any] = field(default_factory=dict)
    # Evening cycles (by their day) on which cooking was seen live.
    cooked: list[str] = field(default_factory=list)
    # The last capacity the battery reported, for when it reports none.
    capacity_kwh: float | None = None

    def add_record(self, record: DayRecord) -> None:
        self.records[record.day.isoformat()] = record
        for key in sorted(self.records)[:-KEEP_DAYS]:
            del self.records[key]

    def mark_cooked(self, day: date) -> bool:
        """Remember that the evening of *day* was a cooking one; return if new."""
        key = day.isoformat()
        if key in self.cooked:
            return False
        self.cooked = [*self.cooked, key][-14:]
        return True

    def record(self, day: date) -> DayRecord | None:
        return self.records.get(day.isoformat())

    def profile(self, subentry_id: str, base: ApplianceProfile) -> ApplianceProfile:
        """The appliance's learned profile on top of its configured defaults."""
        state = self.appliances.get(subentry_id)
        return ApplianceProfile(
            base.default_kwh,
            base.default_hours,
            base.max_kwh,
            list(state.runs) if state else [],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "load": self.load.as_dict(),
            "pv_bias": self.pv_bias.as_dict(),
            "records": [r.as_dict() for _, r in sorted(self.records.items())],
            "snapshots": self.snapshots,
            "cycle": {"day": self.cycle.day, "hours": self.cycle.hours},
            "cooked": self.cooked,
            "appliances": {
                key: {"tracker": s.tracker.as_dict(), "runs": s.runs}
                for key, s in self.appliances.items()
            },
            "last_hour": self.last_hour,
            "upgrade": self.upgrade,
            "capacity_kwh": self.capacity_kwh,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> InsightsData:
        if not data:
            return cls()
        cycle = data.get("cycle") or {}
        hours = cycle.get("hours")
        return cls(
            load=LoadModel.from_dict(data.get("load")),
            pv_bias=PvBias.from_dict(data.get("pv_bias")),
            records={
                r["day"]: DayRecord.from_dict(r)
                for r in data.get("records", [])
                if "day" in r
            },
            snapshots=dict(data.get("snapshots") or {}),
            cycle=EveningCycle(
                cycle.get("day"),
                hours if isinstance(hours, list) and len(hours) == 24 else [None] * 24,
            ),
            cooked=[str(day) for day in data.get("cooked", [])],
            appliances={
                key: ApplianceState(
                    RunTracker.from_dict(value.get("tracker")),
                    [(float(run[0]), float(run[1])) for run in value.get("runs", [])],
                )
                for key, value in (data.get("appliances") or {}).items()
            },
            last_hour=data.get("last_hour"),
            upgrade=dict(data.get("upgrade") or {}),
            capacity_kwh=data.get("capacity_kwh"),
        )


class InsightsStore:
    """Loads and saves InsightsData, delayed so a burst of changes writes once."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.data = InsightsData()

    async def async_load(self) -> InsightsData:
        self.data = InsightsData.from_dict(await self._store.async_load())
        return self.data

    def async_schedule_save(self) -> None:
        self._store.async_delay_save(self.data.as_dict, SAVE_DELAY_S)

    async def async_save(self) -> None:
        await self._store.async_save(self.data.as_dict())

    async def async_remove(self) -> None:
        await self._store.async_remove()
