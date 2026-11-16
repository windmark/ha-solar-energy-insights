"""Each string's share of what the solar counter counted.

The counter is the inverter's own count of the energy it made. String power is
measured on the panel side, before the inverter's losses, and only as often as it
is read, so adding it up never quite meets the counter. Power decides only how
an interval's counted energy is shared between the strings; an error common to
them all, a loss or a missed reading, cancels out in the shares.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime


def split(
    made: Mapping[datetime, float],
    energy: Mapping[int, Mapping[datetime, float]],
) -> dict[int, dict[datetime, float]]:
    """Each string's energy per interval: the counter's energy for the interval
    in the strings' shares of their energy from power. An interval without a
    counter reading, without every string's reading or without any power keeps
    each string's energy from power."""
    out: dict[int, dict[datetime, float]] = {n: {} for n in energy}
    times = sorted({t for rows in energy.values() for t in rows})
    for t in times:
        own = {n: max(rows[t], 0.0) for n, rows in energy.items() if t in rows}
        total = sum(own.values())
        counted = made.get(t)
        scale = (
            max(counted, 0.0) / total
            if counted is not None and total > 0 and len(own) == len(energy)
            else 1.0
        )
        for n, kwh in own.items():
            out[n][t] = kwh * scale
    return out
