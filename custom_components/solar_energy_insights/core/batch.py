"""Many battery runs at once: past nights, battery levels, hidden loads.

Each run starts from its own level and draws its own load; all share the same
half-hour slices and the same sun. The step from one slice to the next depends
on the level, so time is still walked in order, but every run moves together,
as arrays. The rules are those of `battery.simulate`, which a test holds this to.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from numpy.typing import NDArray

from .battery import STEP, Battery

type Floats = NDArray[np.float64]
type Bools = NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class Slices:
    """The half-hour slices from start to end, split at the mark (sunset)."""

    starts: tuple[datetime, ...]
    hours: Floats
    # From the mark on: the night. Without a mark, the whole run.
    night: Bools

    @classmethod
    def between(cls, start: datetime, end: datetime, mark: datetime | None) -> Slices:
        starts, hours, night = [], [], []
        night_start = mark if mark is not None else start
        t = start
        while t < end:
            utc = t.astimezone(UTC)
            base = utc.replace(
                minute=0 if utc.minute < 30 else 30, second=0, microsecond=0
            )
            nxt = min(base + STEP, end)
            if mark is not None and t < mark < nxt:
                nxt = mark
            starts.append(t)
            hours.append((nxt - t).total_seconds() / 3600)
            night.append(t >= night_start)
            t = nxt
        return cls(
            tuple(starts), np.array(hours, dtype=float), np.array(night, dtype=bool)
        )

    def __len__(self) -> int:
        return len(self.starts)

    def sample(self, rate: Callable[[datetime], float]) -> Floats:
        """kW at the start of each slice."""
        return np.array([rate(t) for t in self.starts], dtype=float)


@dataclass(frozen=True, slots=True)
class Runs:
    """What happened in each run, in the night part (from the mark on)."""

    lasts: Bools
    grid_kwh: Floats
    load_kwh: Floats
    spare_kwh: Floats
    # When the battery first handed over to the grid at night; NaT-like: None.
    reserve_at: tuple[datetime | None, ...]
    # The level in % at the start and after each slice: (runs, slices + 1).
    # Only kept when asked for, to draw the runs.
    levels_pct: Floats | None = None


def run(
    battery: Battery,
    socs: Sequence[float] | Floats,
    slices: Slices,
    sun: Floats,
    loads: Floats,
    *,
    record: bool = False,
) -> Runs:
    """Step every run through the slices. *loads* is (runs, slices) kW.

    With *record*, the level of every run after every slice is kept too.
    """
    socs = np.asarray(socs, dtype=float)
    count = len(socs)
    cap, eta, p_max = battery.capacity_kwh, battery.eta, battery.max_power_kw
    floor = battery.floor_kwh
    level = cap * socs / 100
    grid = np.zeros(count)
    load_total = np.zeros(count)
    reserve_hours = np.full(count, np.nan)
    # The lowest level in the night, counting the level as the night begins.
    low = np.where(slices.night[0], level, np.inf) if len(slices) else level.copy()
    elapsed = 0.0
    trace = [level * 100 / cap] if record else []
    for k in range(len(slices)):
        hours = slices.hours[k]
        use = np.maximum(loads[:, k], 0.0)
        net = max(sun[k], 0.0) - use
        # Surplus charges, up to the power limit and room left, with losses.
        charge_kw = np.minimum(np.maximum(net, 0.0), p_max)
        into = np.clip(np.minimum(charge_kw * hours, (cap - level) / eta), 0.0, None)
        level = np.minimum(level + into * eta, cap)
        # A deficit is covered from the battery down to the floor, then the grid.
        need = np.maximum(-net, 0.0) * hours
        out = np.clip(
            np.minimum.reduce(
                [need, np.full(count, p_max * hours), (level - floor) * eta]
            ),
            0.0,
            None,
        )
        level = level - out / eta
        short = np.where(need - out > 1e-6, need - out, 0.0)
        if slices.night[k]:
            load_total += use * hours
            grid += short
            first = np.isnan(reserve_hours) & (short > 0)
            share = np.divide(out, need, out=np.zeros(count), where=need > 0)
            reserve_hours = np.where(first, elapsed + hours * share, reserve_hours)
            low = np.minimum(low, level)
        elif k + 1 < len(slices) and slices.night[k + 1]:
            # The level at the mark itself.
            low = np.minimum(low, level)
        elapsed += hours
        if record:
            trace.append(level * 100 / cap)
    start = slices.starts[0] if len(slices) else None
    reserve_at = tuple(
        None if np.isnan(h) or start is None else start + timedelta(hours=float(h))
        for h in reserve_hours
    )
    return Runs(
        lasts=np.isnan(reserve_hours),
        grid_kwh=grid,
        load_kwh=load_total,
        spare_kwh=np.maximum(np.where(np.isinf(low), level, low) - floor, 0.0),
        reserve_at=reserve_at,
        levels_pct=np.stack(trace, axis=1) if record else None,
    )


def weighted_quantile(values: Floats, weights: Floats, share: float) -> float:
    """The value at *share* of the total weight (nearest rank from below)."""
    order = np.argsort(values, kind="stable")
    cumulative = np.cumsum(weights[order])
    index = int(np.searchsorted(cumulative, share * cumulative[-1] - 1e-12))
    return float(values[order][min(index, len(values) - 1)])
