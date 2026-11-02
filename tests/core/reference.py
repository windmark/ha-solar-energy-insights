"""The original step-by-step battery loop, kept as a reference.

The integration steps the day and the night with one engine (batch.run); this
is the loop that came before it, written independently, line by line. The
engine must agree with it on every result.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.solar_energy_insights.core.battery import (
    STEP,
    Battery,
    Rate,
    Simulation,
    _pct,
)


def _next_boundary(t: datetime) -> datetime:
    """Return the next :00 or :30 after *t*, as an instant."""
    utc = t.astimezone(UTC)
    base = utc.replace(minute=0 if utc.minute < 30 else 30, second=0, microsecond=0)
    return base + STEP


def simulate_steps(
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
            out = max(min(need, p_max * hours, (level - floor) * eta), 0.0)
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
