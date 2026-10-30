"""Turn plans and simulations into JSON for services and the WebSocket API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .core.battery import Simulation
from .core.planner import Plan


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def plan_dict(plan: Plan, *, curve: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "at": _iso(plan.now),
        "phase": str(plan.phase),
        "grid_mode": plan.grid_mode,
        "headline": plan.headline,
        "subline": plan.subline,
        "tone": str(plan.tone),
        "lines": [
            {
                "key": line.key,
                "label": line.label,
                "text": line.text,
                "detail": line.detail,
                "tone": str(line.tone),
                "icon": line.icon,
            }
            for line in plan.lines
        ],
        "full_at": _iso(plan.full_at),
        "peak_pct": _r(plan.peak_pct, 0),
        "peak_at": _iso(plan.peak_at),
        "sunset_pct": _r(plan.sunset_pct, 0),
        "lasts_night": plan.lasts_night,
        "lasts_night_any_evening": plan.lasts_night_any_evening,
        "reserve_at": _iso(plan.reserve_at),
        "night_load_kwh": _r(plan.night_load_kwh),
        "night_grid_kwh": _r(plan.night_grid_kwh),
        "night_spare_kwh": _r(plan.night_spare_kwh),
        "free_power": plan.free_power,
        "free_until": _iso(plan.free_until),
        "best_window": {
            "start": plan.best_window.start.isoformat(),
            "end": plan.best_window.end.isoformat(),
            "kwh": _r(plan.best_window.kwh),
        }
        if plan.best_window
        else None,
        "sun_covers_house_at": _iso(plan.sun_covers_house_at),
    }
    if curve:
        data["curve"] = [[t.isoformat(), round(pct, 1)] for t, pct in plan.curve]
    return data


def simulation_dict(sim: Simulation) -> dict[str, Any]:
    return {
        "full_at": _iso(sim.full_at),
        "reserve_at": _iso(sim.reserve_at),
        "lasts": sim.lasts,
        "grid_kwh": _r(sim.grid_kwh),
        "export_kwh": _r(sim.export_kwh + sim.export_before_full_kwh),
        "pv_kwh": _r(sim.pv_kwh),
        "load_kwh": _r(sim.load_kwh),
        "peak_pct": _r(sim.peak_pct, 0),
        "end_pct": _r(sim.end_pct, 0),
        "sunset_pct": _r(sim.mark_pct, 0),
        "spare_kwh": _r(sim.spare_kwh),
        "points": [[t.isoformat(), round(pct, 1)] for t, pct in sim.points],
    }
