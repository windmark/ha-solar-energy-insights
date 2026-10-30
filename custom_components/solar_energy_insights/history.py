"""Read the recorder's long-term and short-term statistics.

Long-term statistics are the source of truth for energy history: hourly ``change``
handles daily-reset and lifetime counters alike, and survives restarts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Final, Literal

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

type Period = Literal["5minute", "hour", "day"]
type StatType = Literal["change", "mean", "min", "max"]
type Series = dict[datetime, float]

UNITS: Final = {"energy": "kWh", "power": "kW"}


async def async_series(
    hass: HomeAssistant,
    entity_ids: Iterable[str],
    start: datetime,
    end: datetime,
    period: Period,
    stat: StatType,
) -> dict[str, Series]:
    """Return {entity_id: {period start: value}} for the ids that have statistics."""
    ids = {entity_id for entity_id in entity_ids if entity_id}
    if not ids:
        return {}
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        ids,
        period,
        dict(UNITS),
        {stat},
    )
    out: dict[str, Series] = {}
    for entity_id, values in rows.items():
        series: Series = {}
        for row in values:
            value = row.get(stat)
            if value is not None:
                series[dt_util.utc_from_timestamp(row["start"])] = float(value)
        out[entity_id] = series
    return out


def total(
    series: Mapping[datetime, float] | None, start: datetime, end: datetime
) -> float | None:
    """Sum a series over [start, end); None when nothing was recorded."""
    if not series:
        return None
    values = [value for t, value in series.items() if start <= t < end]
    return sum(values) if values else None


def extreme(
    series: Mapping[datetime, float] | None,
    start: datetime,
    end: datetime,
    *,
    highest: bool,
) -> float | None:
    if not series:
        return None
    values = [value for t, value in series.items() if start <= t < end]
    if not values:
        return None
    return max(values) if highest else min(values)
