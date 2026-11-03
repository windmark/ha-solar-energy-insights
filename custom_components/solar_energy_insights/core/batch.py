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
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .battery import FULL_WITHIN_PCT, STEP, Battery

type Floats = NDArray[np.float64]
type Bools = NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class Slices:
    """The half-hour slices from start to end, split at the mark (sunset)."""

    starts: tuple[datetime, ...]
    hours: Floats
    # From the mark on, or the whole run without one.
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
    reserve_at: tuple[datetime | None, ...]
    # (runs, slices + 1): at the start and after each slice.
    levels_pct: Floats | None = None
    # With detail: the whole run, as battery.simulate reports it.
    detail: dict[str, Any] | None = None


def run(
    battery: Battery,
    socs: Sequence[float] | Floats,
    slices: Slices,
    sun: Floats,
    loads: Floats,
    *,
    record: bool = False,
    detail: bool = False,
) -> Runs:
    """Step every run through the slices; *loads* is (runs, slices) kW.

    *record* keeps every level; *detail* keeps the bookkeeping `simulate` reports,
    which the nightly batch doesn't need.
    """
    socs = np.asarray(socs, dtype=float)
    count = len(socs)
    cap, eta, p_max = battery.capacity_kwh, battery.eta, battery.max_power_kw
    floor = battery.floor_kwh
    top = battery.ceiling_kwh
    level = cap * socs / 100
    grid = np.zeros(count)
    load_total = np.zeros(count)
    reserve_hours = np.full(count, np.nan)
    # The lowest level in the night, counting the level as the night begins.
    low = np.where(slices.night[0], level, np.inf) if len(slices) else level.copy()
    elapsed = 0.0
    trace = [level * 100 / cap] if record else []
    if detail:
        full_h = np.where(
            level >= top - battery.capacity_kwh * FULL_WITHIN_PCT / 100, 0.0, np.nan
        )
        export = np.zeros(count)
        export_before = np.zeros(count)
        grid_all = np.zeros(count)
        pv_total = 0.0
        load_all = np.zeros(count)
        first_short_h = np.full(count, np.nan)
        peak, peak_k = level.copy(), np.full(count, -1)
        low_all = level.copy()
        mark_level = (
            level.copy() if len(slices) and slices.night[0] else np.full(count, np.nan)
        )
    for k in range(len(slices)):
        hours = slices.hours[k]
        use = np.maximum(loads[:, k], 0.0)
        net = max(sun[k], 0.0) - use
        charge_kw = np.minimum(np.maximum(net, 0.0), p_max)
        into = np.clip(np.minimum(charge_kw * hours, (top - level) / eta), 0.0, None)
        level = np.maximum(np.minimum(level + into * eta, top), level)
        if detail:
            pv_total += max(sun[k], 0.0) * hours
            load_all += use * hours
            surplus = net > 0
            filled = surplus & (into < charge_kw * hours - 1e-9)
            filled_h = np.divide(
                into, charge_kw, out=np.zeros(count), where=charge_kw > 0
            )
            was_full = ~np.isnan(full_h)
            over = np.maximum(net - charge_kw, 0.0)
            # Filling within this slice: before that, only the excess over the power
            # limit is exported; after it, all of the surplus.
            export += np.where(
                filled,
                np.where(was_full, over * filled_h, 0.0) + net * (hours - filled_h),
                0.0,
            )
            export_before += np.where(filled & ~was_full, over * filled_h, 0.0)
            export += np.where(surplus & ~filled & was_full, over * hours, 0.0)
            export_before += np.where(surplus & ~filled & ~was_full, over * hours, 0.0)
            full_h = np.where(filled & ~was_full, elapsed + filled_h, full_h)
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
        if detail:
            grid_all += short
            share_all = np.divide(out, need, out=np.zeros(count), where=need > 0)
            first_short_h = np.where(
                np.isnan(first_short_h) & (short > 0),
                elapsed + hours * share_all,
                first_short_h,
            )
            higher = level > peak + 1e-6
            peak = np.where(higher, level, peak)
            peak_k = np.where(higher, k, peak_k)
            low_all = np.minimum(low_all, level)
            # The level at the mark: after the last slice before the night.
            if not slices.night[k] and k + 1 < len(slices) and slices.night[k + 1]:
                mark_level = level.copy()
        if slices.night[k]:
            load_total += use * hours
            grid += short
            first = np.isnan(reserve_hours) & (short > 0)
            share = np.divide(out, need, out=np.zeros(count), where=need > 0)
            reserve_hours = np.where(first, elapsed + hours * share, reserve_hours)
            low = np.minimum(low, level)
        elif k + 1 < len(slices) and slices.night[k + 1]:
            low = np.minimum(low, level)
        elapsed += hours
        if record:
            trace.append(level * 100 / cap)
    start = slices.starts[0] if len(slices) else None

    def at(h: float) -> datetime | None:
        return (
            None if np.isnan(h) or start is None else start + timedelta(hours=float(h))
        )

    books = None
    if detail and start is not None:
        ends = [
            t + timedelta(hours=float(h))
            for t, h in zip(slices.starts, slices.hours, strict=True)
        ]
        books = {
            "full_at": [at(h) for h in full_h],
            "export_kwh": export,
            "export_before_full_kwh": export_before,
            "grid_kwh": grid_all,
            "pv_kwh": pv_total,
            "load_kwh": load_all,
            "reserve_at": [at(h) for h in first_short_h],
            "peak_kwh": peak,
            "peak_at": [start if k < 0 else ends[k] for k in peak_k],
            "low_kwh": low_all,
            "end_kwh": level,
            "mark_kwh": mark_level,
            "night_low_kwh": np.where(np.isinf(low), level, low),
            "ends": ends,
        }
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
        detail=books,
    )


def weighted_quantile(values: Floats, weights: Floats, share: float) -> float:
    """The value at *share* of the total weight (nearest rank from below)."""
    order = np.argsort(values, kind="stable")
    cumulative = np.cumsum(weights[order])
    index = int(np.searchsorted(cumulative, share * cumulative[-1] - 1e-12))
    return float(values[order][min(index, len(values) - 1)])
