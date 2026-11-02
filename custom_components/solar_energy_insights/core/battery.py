"""Step a home battery through a forecast of solar and house load."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

# kW at an instant.
type Rate = Callable[[datetime], float]

STEP = timedelta(minutes=30)
# Within this many points of its charge limit, a battery counts as full.
FULL_MARGIN_PCT = 0.5


@dataclass(frozen=True, slots=True)
class Battery:
    """What the simulation needs to know about the battery right now."""

    capacity_kwh: float
    soc_pct: float
    floor_pct: float
    max_power_kw: float = 5.0
    round_trip: float = 0.92
    # The level charging stops at: 100, or lower when the battery is set to
    # spare its cells. "Full" means reaching it.
    ceiling_pct: float = 100.0

    @property
    def eta(self) -> float:
        """One-way efficiency: charging and discharging lose the same share."""
        return math.sqrt(self.round_trip)

    @property
    def stored_kwh(self) -> float:
        return self.capacity_kwh * self.soc_pct / 100

    @property
    def floor_kwh(self) -> float:
        return self.capacity_kwh * self.floor_pct / 100

    @property
    def usable_kwh(self) -> float:
        """Energy above the reserve floor, as stored in the cells."""
        return max(self.stored_kwh - self.floor_kwh, 0.0)

    @property
    def ceiling_kwh(self) -> float:
        return self.capacity_kwh * self.ceiling_pct / 100

    @property
    def is_full(self) -> bool:
        return self.soc_pct >= self.ceiling_pct - FULL_MARGIN_PCT


@dataclass(frozen=True, slots=True)
class Simulation:
    """The battery's path from the start to the end of a simulation.

    Energy is in kWh. Exports are split at the moment the battery fills, since
    export before full means charging was power-limited, after full it is surplus.
    """

    capacity_kwh: float
    floor_kwh: float
    full_at: datetime | None
    export_kwh: float
    export_before_full_kwh: float
    reserve_at: datetime | None
    grid_kwh: float
    pv_kwh: float
    load_kwh: float
    peak_kwh: float
    peak_at: datetime
    low_kwh: float
    end_kwh: float
    mark_kwh: float | None
    # The lowest level from the mark on, such as overnight after sunset.
    low_after_mark_kwh: float | None
    # From the mark on (or the whole run without one): the night, in the day.
    night_reserve_at: datetime | None
    night_grid_kwh: float
    night_load_kwh: float
    points: tuple[tuple[datetime, float], ...] = field(repr=False)

    @property
    def peak_pct(self) -> float:
        return _pct(self.peak_kwh, self.capacity_kwh)

    @property
    def end_pct(self) -> float:
        return _pct(self.end_kwh, self.capacity_kwh)

    @property
    def mark_pct(self) -> float | None:
        return None if self.mark_kwh is None else _pct(self.mark_kwh, self.capacity_kwh)

    @property
    def spare_kwh(self) -> float:
        """Usable energy left at the lowest point, above the reserve floor.

        With a mark, only the lowest point after it counts: tonight's spare is not
        about this morning's low.
        """
        low = (
            self.low_after_mark_kwh
            if self.low_after_mark_kwh is not None
            else self.low_kwh
        )
        return max(low - self.floor_kwh, 0.0)

    @property
    def lasts(self) -> bool:
        """Whether the battery never had to hand over to the grid."""
        return self.reserve_at is None

    @property
    def night_lasts(self) -> bool:
        """Whether the battery got from the mark (sunset) to the end without the
        grid. Reaching the reserve before the mark, such as this morning before
        the sun takes over, says nothing about the night."""
        return self.night_reserve_at is None


def _pct(kwh: float, capacity_kwh: float) -> float:
    return kwh / capacity_kwh * 100 if capacity_kwh > 0 else 0.0


def simulate(
    battery: Battery,
    start: datetime,
    end: datetime,
    pv: Rate,
    load: Rate,
    mark_at: datetime | None = None,
) -> Simulation:
    """Step the battery from *start* to *end* in slices of at most 30 minutes.

    Surplus solar charges the battery up to its power limit, with charging losses,
    and the rest is exported. A deficit is covered from the battery down to the
    reserve floor, then from the grid. *mark_at* records the level at that moment,
    such as sunset.

    It is one battery through the same engine as the nights (batch.run), so the
    day and the night are stepped by the same code.
    """
    from . import batch

    cap, floor = battery.capacity_kwh, battery.floor_kwh
    level = battery.stored_kwh
    slices = batch.Slices.between(start, end, mark_at)
    if not len(slices):
        at_mark = level if mark_at is not None and mark_at <= start else None
        return Simulation(
            capacity_kwh=cap,
            floor_kwh=floor,
            full_at=start if battery.is_full else None,
            export_kwh=0.0,
            export_before_full_kwh=0.0,
            reserve_at=None,
            grid_kwh=0.0,
            pv_kwh=0.0,
            load_kwh=0.0,
            peak_kwh=level,
            peak_at=start,
            low_kwh=level,
            end_kwh=level,
            mark_kwh=at_mark,
            low_after_mark_kwh=at_mark,
            night_reserve_at=None,
            night_grid_kwh=0.0,
            night_load_kwh=0.0,
            points=((start, _pct(level, cap)),),
        )
    runs = batch.run(
        battery,
        [battery.soc_pct],
        slices,
        slices.sample(pv),
        slices.sample(load)[np.newaxis, :],
        record=True,
        detail=True,
    )
    d = runs.detail
    assert d is not None
    assert runs.levels_pct is not None
    end_kwh = float(d["end_kwh"][0])
    mark: float | None = None
    low_after_mark: float | None = None
    if mark_at is not None and mark_at <= end:
        found = float(d["mark_kwh"][0])
        # A mark at the very end (the day stepped to sunset) is the level there.
        mark = end_kwh if np.isnan(found) else found
        low_after_mark = float(d["night_low_kwh"][0])
    times = (slices.starts[0], *d["ends"])
    return Simulation(
        capacity_kwh=cap,
        floor_kwh=floor,
        full_at=d["full_at"][0],
        export_kwh=float(d["export_kwh"][0]),
        export_before_full_kwh=float(d["export_before_full_kwh"][0]),
        reserve_at=d["reserve_at"][0],
        grid_kwh=float(d["grid_kwh"][0]),
        pv_kwh=float(d["pv_kwh"]),
        load_kwh=float(d["load_kwh"][0]),
        peak_kwh=float(d["peak_kwh"][0]),
        peak_at=d["peak_at"][0],
        low_kwh=float(d["low_kwh"][0]),
        end_kwh=end_kwh,
        mark_kwh=mark,
        low_after_mark_kwh=low_after_mark,
        night_reserve_at=runs.reserve_at[0],
        night_grid_kwh=float(runs.grid_kwh[0]),
        night_load_kwh=float(runs.load_kwh[0]),
        points=tuple(
            (t, float(v)) for t, v in zip(times, runs.levels_pct[0], strict=True)
        ),
    )
