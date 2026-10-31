"""Step a home battery through a forecast of solar and house load."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

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
    # The battery's own limit on discharging, when it differs from charging;
    # None means the same as max_power_kw.
    max_discharge_kw: float | None = None

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
    def discharge_kw(self) -> float:
        return (
            self.max_power_kw
            if self.max_discharge_kw is None
            else self.max_discharge_kw
        )

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


def _next_boundary(t: datetime) -> datetime:
    """Return the next :00 or :30 after *t*, as an instant."""
    utc = t.astimezone(UTC)
    base = utc.replace(minute=0 if utc.minute < 30 else 30, second=0, microsecond=0)
    return base + STEP


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
    """
    cap, eta, p_max = battery.capacity_kwh, battery.eta, battery.max_power_kw
    p_out = battery.discharge_kw
    floor = battery.floor_kwh
    top = battery.ceiling_kwh
    level = battery.stored_kwh
    full_at = start if battery.is_full else None
    export = export_before = grid = pv_total = load_total = 0.0
    reserve_at: datetime | None = None
    peak, peak_at, low = level, start, level
    mark = level if mark_at is not None and mark_at <= start else None
    low_after_mark = mark
    night_start = mark_at if mark_at is not None else start
    night_reserve_at: datetime | None = None
    night_grid = night_load = 0.0
    points: list[tuple[datetime, float]] = [(start, _pct(level, cap))]

    t = start
    while t < end:
        nxt = min(_next_boundary(t), end)
        if mark_at is not None and t < mark_at < nxt:
            nxt = mark_at
        hours = (nxt - t).total_seconds() / 3600
        sun, use = max(pv(t), 0.0), max(load(t), 0.0)
        net = sun - use
        pv_total += sun * hours
        load_total += use * hours
        in_night = t >= night_start
        if in_night:
            night_load += use * hours

        if net > 0:
            charge_kw = min(net, p_max)
            into = max(min(charge_kw * hours, (top - level) / eta), 0.0)
            level = max(min(level + into * eta, top), level)
            if into < charge_kw * hours - 1e-9:
                # The battery filled during this slice.
                filled_h = into / charge_kw
                if full_at is not None:
                    export += (net - charge_kw) * filled_h + net * (hours - filled_h)
                else:
                    export_before += (net - charge_kw) * filled_h
                    export += net * (hours - filled_h)
                    full_at = t + timedelta(hours=filled_h)
            elif full_at is not None:
                export += (net - charge_kw) * hours
            else:
                export_before += (net - charge_kw) * hours
        elif net < 0:
            need = -net * hours
            out = max(min(need, p_out * hours, (level - floor) * eta), 0.0)
            level -= out / eta
            if out < need - 1e-6:
                grid += need - out
                handover = t + (nxt - t) * (out / need)
                if reserve_at is None:
                    reserve_at = handover
                if in_night:
                    night_grid += need - out
                    if night_reserve_at is None:
                        night_reserve_at = handover

        if level > peak + 1e-6:
            peak, peak_at = level, nxt
        low = min(low, level)
        if mark_at is not None and mark is None and nxt >= mark_at:
            mark = level
        if mark is not None and nxt >= (mark_at or start):
            low_after_mark = (
                level if low_after_mark is None else min(low_after_mark, level)
            )
        points.append((nxt, _pct(level, cap)))
        t = nxt

    return Simulation(
        capacity_kwh=cap,
        floor_kwh=floor,
        full_at=full_at,
        export_kwh=export,
        export_before_full_kwh=export_before,
        reserve_at=reserve_at,
        grid_kwh=grid,
        pv_kwh=pv_total,
        load_kwh=load_total,
        peak_kwh=peak,
        peak_at=peak_at,
        low_kwh=low,
        end_kwh=level,
        mark_kwh=mark,
        low_after_mark_kwh=low_after_mark,
        night_reserve_at=night_reserve_at,
        night_grid_kwh=night_grid,
        night_load_kwh=night_load,
        points=tuple(points),
    )
