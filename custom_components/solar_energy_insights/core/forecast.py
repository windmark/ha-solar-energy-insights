"""Solcast's half-hourly PV forecast, and a learned correction for this roof."""

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


# A correction cell starts as this many kWh of forecast that came true exactly, so
# a few odd days cannot swing it.
PRIOR_KWH = 3.0
# Each day's observation weighs this much more than the last, about two months.
DECAY = 0.97
MIN_FACTOR, MAX_FACTOR = 0.6, 1.4
# Days of paired errors before the correction may be switched on.
MIN_SCORED_DAYS = 14


@dataclass(slots=True)
class PvBias:
    """How the roof's real output compares to Solcast, per month and clock hour.

    The correction is only applied once it has beaten the raw forecast on the
    days scored so far: a model earns its place by its error, not by existing.
    """

    actual: dict[str, float]
    forecast: dict[str, float]
    err_raw: float = 0.0
    err_corrected: float = 0.0
    scored_days: int = 0

    @classmethod
    def empty(cls) -> PvBias:
        return cls({}, {})

    @staticmethod
    def _key(month: int, hour: int) -> str:
        return f"{month}:{hour}"

    def factor(self, month: int, hour: int) -> float:
        key = self._key(month, hour)
        a, f = self.actual.get(key, 0.0), self.forecast.get(key, 0.0)
        return min(max((a + PRIOR_KWH) / (f + PRIOR_KWH), MIN_FACTOR), MAX_FACTOR)

    @property
    def active(self) -> bool:
        return (
            self.scored_days >= MIN_SCORED_DAYS and self.err_corrected < self.err_raw
        )

    @property
    def improvement_pct(self) -> float | None:
        """How much smaller the corrected error is, once anything is scored."""
        if not self.scored_days or self.err_raw <= 0:
            return None
        return (1 - self.err_corrected / self.err_raw) * 100

    def observe_day(self, month: int, forecast: list[float], actual: list[float]) -> None:
        """Score yesterday's forecast with and without the correction, then learn.

        Scoring happens before learning, so the correction is always judged on a
        day it had not seen.
        """
        raw = corrected = 0.0
        for hour, (f, a) in enumerate(zip(forecast, actual, strict=True)):
            raw += abs(a - f)
            corrected += abs(a - f * self.factor(month, hour))
        if sum(forecast) > 0.5 or sum(actual) > 0.5:
            self.err_raw += raw
            self.err_corrected += corrected
            self.scored_days += 1
        for hour, (f, a) in enumerate(zip(forecast, actual, strict=True)):
            if f <= 0 and a <= 0:
                continue
            key = self._key(month, hour)
            self.actual[key] = self.actual.get(key, 0.0) * DECAY + a
            self.forecast[key] = self.forecast.get(key, 0.0) * DECAY + f

    def apply(self, forecast: PvForecast, tz: tzinfo) -> PvForecast:
        if not self.active:
            return forecast

        def factor(start: datetime) -> float:
            local = start.astimezone(tz)
            return self.factor(local.month, local.hour)

        return forecast.scaled(factor)

    def as_dict(self) -> dict[str, Any]:
        return {
            "actual": self.actual,
            "forecast": self.forecast,
            "err_raw": self.err_raw,
            "err_corrected": self.err_corrected,
            "scored_days": self.scored_days,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> PvBias:
        if not data:
            return cls.empty()
        return cls(
            dict(data.get("actual", {})),
            dict(data.get("forecast", {})),
            float(data.get("err_raw", 0.0)),
            float(data.get("err_corrected", 0.0)),
            int(data.get("scored_days", 0)),
        )
