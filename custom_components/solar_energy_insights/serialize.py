"""The plan as JSON, for the action, the WebSocket API and diagnostics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .core.plan import NightOutcome, Plan


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _outcome(o: NightOutcome) -> dict[str, Any]:
    return {
        "days_ago": o.days_ago,
        "lasts": o.lasts,
        "reserve_at": _iso(o.reserve_at),
        "load_kwh": round(o.load_kwh, 2),
        "grid_kwh": round(o.grid_kwh, 2),
        "spare_kwh": round(o.spare_kwh, 2),
    }


def plan_dict(plan: Plan, *, detail: bool = True) -> dict[str, Any]:
    night = plan.night
    data: dict[str, Any] = {
        "at": _iso(plan.now),
        "sunrise": _iso(plan.sunrise),
        "sunset": _iso(plan.sunset),
        "charge": {
            "full_at": _iso(plan.charge.full_at),
            "full_at_cloudy": _iso(plan.charge.full_at_cloudy),
            "peak_pct": round(plan.charge.peak_pct),
            "peak_pct_cloudy": round(plan.charge.peak_pct_cloudy),
            "sunset_pct": None
            if plan.charge.sunset_pct is None
            else round(plan.charge.sunset_pct),
        },
        "night": None
        if night is None
        else {
            "nights": len(night.outcomes),
            "lasting": night.lasting,
            "typical": _outcome(night.typical),
            "heavy": _outcome(night.heavy),
        },
        "best_window": None
        if plan.best_window is None
        else {
            "start": _iso(plan.best_window.start),
            "end": _iso(plan.best_window.end),
            "kwh": round(plan.best_window.kwh, 2),
        },
        "free_power": plan.free_power,
    }
    if detail:
        if night is not None:
            data["night"]["each"] = [_outcome(o) for o in night.outcomes]
        data["curve"] = [[t.isoformat(), round(pct, 1)] for t, pct in plan.curve]
    return data
