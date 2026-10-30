"""Detect appliance runs from a smart plug and learn their energy and length."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

RUNNING_W = 10.0
# Above the threshold this long before a run counts, so a blip is ignored.
START_AFTER = timedelta(minutes=2)
# Below the threshold this long before a run ends; programs pause to soak or cool.
END_AFTER = timedelta(minutes=15)
MIN_RUN_H, MAX_RUN_H = 0.2, 8.0
MIN_RUN_KWH = 0.05
KEEP_RUNS = 10
# The learned value is the mean of this many latest runs.
AVERAGE_RUNS = 5


@dataclass(frozen=True, slots=True)
class Run:
    started: datetime
    ended: datetime
    kwh: float

    @property
    def hours(self) -> float:
        return (self.ended - self.started).total_seconds() / 3600


@dataclass(slots=True)
class RunTracker:
    """Turns a stream of power readings into completed runs.

    Energy comes from the plug's own counter where there is one, else from the
    power readings, integrated between them.
    """

    first_above: datetime | None = None
    last_above: datetime | None = None
    running: bool = False
    start_kwh: float | None = None
    integrated_kwh: float = 0.0
    last_sample: tuple[datetime, float] | None = None

    def feed(self, t: datetime, watts: float, meter_kwh: float | None) -> Run | None:
        if self.last_sample is not None and self.first_above is not None:
            previous_t, previous_w = self.last_sample
            self.integrated_kwh += previous_w * (t - previous_t).total_seconds() / 3.6e6
        self.last_sample = (t, max(watts, 0.0))

        if watts > RUNNING_W:
            if self.first_above is None:
                self.first_above, self.start_kwh, self.integrated_kwh = (
                    t,
                    meter_kwh,
                    0.0,
                )
            self.last_above = t
            if not self.running and t - self.first_above >= START_AFTER:
                self.running = True
            return None

        if self.first_above is None or self.last_above is None:
            return None
        if not self.running:
            # Too short to be a run.
            self._reset()
            return None
        if t - self.last_above < END_AFTER:
            return None
        if self.start_kwh is not None and meter_kwh is not None:
            kwh = meter_kwh - self.start_kwh
        else:
            kwh = self.integrated_kwh
        run = Run(self.first_above, self.last_above, kwh)
        self._reset()
        return run

    def _reset(self) -> None:
        self.first_above = self.last_above = None
        self.running = False
        self.start_kwh = None
        self.integrated_kwh = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "first_above": _iso(self.first_above),
            "last_above": _iso(self.last_above),
            "running": self.running,
            "start_kwh": self.start_kwh,
            "integrated_kwh": self.integrated_kwh,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> RunTracker:
        if not data:
            return cls()
        return cls(
            first_above=_time(data.get("first_above")),
            last_above=_time(data.get("last_above")),
            running=bool(data.get("running")),
            start_kwh=data.get("start_kwh"),
            integrated_kwh=float(data.get("integrated_kwh", 0.0)),
        )


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _time(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


@dataclass(slots=True)
class ApplianceProfile:
    """What one run of an appliance takes, learned from its plausible runs."""

    default_kwh: float
    default_hours: float
    max_kwh: float
    runs: list[tuple[float, float]] = field(default_factory=list)

    def add(self, run: Run) -> bool:
        """Keep a run that looks real; return whether it was kept."""
        if not (MIN_RUN_KWH < run.kwh <= self.max_kwh):
            return False
        if not (MIN_RUN_H < run.hours < MAX_RUN_H):
            return False
        self.runs = [*self.runs, (round(run.kwh, 3), round(run.hours, 2))][-KEEP_RUNS:]
        return True

    @property
    def learned(self) -> bool:
        return bool(self.runs)

    @property
    def kwh(self) -> float:
        recent = self.runs[-AVERAGE_RUNS:]
        return sum(k for k, _ in recent) / len(recent) if recent else self.default_kwh

    @property
    def hours(self) -> float:
        recent = self.runs[-AVERAGE_RUNS:]
        return sum(h for _, h in recent) / len(recent) if recent else self.default_hours

    def load_kw(self, since_start: timedelta) -> float:
        """The average power of a run, while one started *since_start* ago lasts."""
        hours = since_start.total_seconds() / 3600
        return self.kwh / self.hours if 0 <= hours < self.hours else 0.0
