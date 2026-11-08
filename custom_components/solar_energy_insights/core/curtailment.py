"""Solar the inverter held back today because the grid took no more.

The inverter doesn't report what it curtailed. While the export sits at the
feed-in cap, the solar it could have made is the forecast for that moment; what
it made is the reading. The difference, over the minutes at the cap, is the
estimate. Away from the cap nothing is counted: a forecast that misses on a
cloudy hour is not curtailment.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .forecast import PvForecast

# An export this close to the cap counts as held at it: the inverter keeps a
# little under the cap so that it is never exceeded.
AT_CAP_SHARE = 0.97


@dataclass(frozen=True, slots=True)
class Reading:
    """The mean export and solar power over a stretch of time, in kW."""

    start: datetime
    minutes: float
    export_kw: float
    solar_kw: float


@dataclass(frozen=True, slots=True)
class Curtailed:
    kwh: float
    minutes_at_cap: float


def curtailed(readings: Iterable[Reading], pv: PvForecast, cap_kw: float) -> Curtailed:
    """The solar held back over *readings*, estimated from the forecast."""
    kwh = minutes = 0.0
    if cap_kw <= 0:
        return Curtailed(0.0, 0.0)
    for r in readings:
        if r.export_kw < cap_kw * AT_CAP_SHARE:
            continue
        minutes += r.minutes
        middle = r.start + timedelta(minutes=r.minutes / 2)
        kwh += max(pv.kw_at(middle) - r.solar_kw, 0.0) * r.minutes / 60
    return Curtailed(round(kwh, 3), minutes)
