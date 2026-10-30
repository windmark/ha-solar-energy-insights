"""Synthetic days for the core tests."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.solar_energy_insights.core.forecast import PvForecast, Slot

TZ = ZoneInfo("Europe/Berlin")


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ).astimezone(
        UTC
    )


def sunny_slots(
    day: date, peak_kw: float, rise: int = 6, hours: int = 15
) -> list[Slot]:
    """A clean bell from *rise* over *hours*, as Solcast half hours."""
    count = hours * 2
    slots = []
    for k in range(count):
        kw = peak_kw * math.sin(math.pi * (k + 0.5) / count)
        slots.append(
            Slot(at(day, rise) + timedelta(minutes=30 * k), kw, kw * 0.6, kw * 1.1)
        )
    return slots


def sunny(*days: date, peak_kw: float = 8.0) -> PvForecast:
    return PvForecast(slot for day in days for slot in sunny_slots(day, peak_kw))


def constant(kw: float):
    return lambda _t: kw
