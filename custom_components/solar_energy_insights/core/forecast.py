"""Solcast's half-hourly PV forecast."""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Any

SLOT = timedelta(minutes=30)


class Quantile(StrEnum):
    """Solcast's estimates: the median and the 10th and 90th percentiles."""

    P50 = "pv_estimate"
    P10 = "pv_estimate10"
    P90 = "pv_estimate90"


@dataclass(frozen=True, slots=True)
class Slot:
    """Average kW over the half hour from *start*."""

    start: datetime
    p50: float
    p10: float
    p90: float

    def kw(self, quantile: Quantile) -> float:
        match quantile:
            case Quantile.P10:
                return self.p10
            case Quantile.P90:
                return self.p90
            case _:
                return self.p50


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else None
    return None


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


class PvForecast:
    """A run of half-hour slots, looked up by instant."""

    def __init__(self, slots: Iterable[Slot]) -> None:
        unique = {slot.start: slot for slot in slots}
        self._slots = sorted(unique.values(), key=lambda slot: slot.start)
        self._starts = [slot.start for slot in self._slots]

    @classmethod
    def from_solcast(cls, *detailed: Iterable[Mapping[str, Any]] | None) -> PvForecast:
        """Build from one or more Solcast ``detailedForecast`` attributes."""
        slots = []
        for periods in detailed:
            for period in periods or ():
                start = _parse_time(period.get("period_start"))
                if start is None:
                    continue
                p50 = _number(period.get(Quantile.P50))
                slots.append(
                    Slot(
                        start,
                        p50,
                        _number(period.get(Quantile.P10, p50)),
                        _number(period.get(Quantile.P90, p50)),
                    )
                )
        return cls(slots)

    def __len__(self) -> int:
        return len(self._slots)

    @property
    def slots(self) -> tuple[Slot, ...]:
        return tuple(self._slots)

    def kw_at(self, t: datetime, quantile: Quantile = Quantile.P50) -> float:
        """Return the forecast kW for the slot holding *t*, 0 outside the forecast."""
        index = bisect.bisect_right(self._starts, t) - 1
        if index < 0:
            return 0.0
        slot = self._slots[index]
        return slot.kw(quantile) if t < slot.start + SLOT else 0.0

    def energy(
        self, start: datetime, end: datetime, quantile: Quantile = Quantile.P50
    ) -> float:
        """Return the forecast kWh between two instants, counting part slots."""
        total = 0.0
        for slot in self._slots:
            overlap = min(end, slot.start + SLOT) - max(start, slot.start)
            if overlap > timedelta(0):
                total += slot.kw(quantile) * overlap.total_seconds() / 3600
        return total

    def hourly(self, day: date, tz: tzinfo) -> list[float]:
        """Return the forecast kWh per local clock hour of *day*."""
        hours = [0.0] * 24
        for slot in self._slots:
            local = slot.start.astimezone(tz)
            if local.date() == day:
                hours[local.hour] += slot.p50 * SLOT.total_seconds() / 3600
        return hours

    def scaled(self, factor: Any) -> PvForecast:
        """Return the forecast with each slot multiplied by ``factor(start)``."""
        return PvForecast(
            Slot(s.start, s.p50 * (f := factor(s.start)), s.p10 * f, s.p90 * f)
            for s in self._slots
        )
