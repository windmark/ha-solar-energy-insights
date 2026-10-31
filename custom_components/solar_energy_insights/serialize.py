"""The plan as JSON, for the action, the WebSocket API and diagnostics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .core.backtest import Calibration
from .core.plan import NightOutcome, Plan


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _outcome(o: NightOutcome) -> dict[str, Any]:
    return {
        "days_ago": o.days_ago,
        "weight": round(o.weight, 3),
        "lasts": o.lasts,
        "reserve_at": _iso(o.reserve_at),
        "load_kwh": round(o.load_kwh, 2),
        "grid_kwh": round(o.grid_kwh, 2),
        "spare_kwh": round(o.spare_kwh, 2),
    }


def costs(plan: Plan, price: float, feed_in: float) -> dict[str, Any]:
    """The plan in money, at a price per kWh bought and a tariff per kWh exported.

    The price applies to every hour ahead as it is now: nothing is timed.
    """
    night = plan.night
    surplus = plan.surplus.total_kwh
    return {
        "price": round(price, 4),
        "feed_in": round(feed_in, 4),
        "night_grid": None
        if night is None
        else round(night.typical.grid_kwh * price, 2),
        "night_grid_heavy": None
        if night is None
        else round(night.heavy.grid_kwh * price, 2),
        "surplus_saving": round(surplus * max(price - feed_in, 0.0), 2),
        "surplus_export_value": round(surplus * feed_in, 2),
    }


def plan_dict(
    plan: Plan,
    *,
    detail: bool = True,
    prices: tuple[float, float] | None = None,
) -> dict[str, Any]:
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
            "outlook": str(night.outlook),
            "lasting": night.lasting,
            "typical": _outcome(night.typical),
            "heavy": _outcome(night.heavy),
        },
        "surplus": {
            "kwh": round(plan.surplus.total_kwh, 2),
            "after_full_kwh": round(plan.surplus.after_full_kwh, 2),
            "while_charging_kwh": round(plan.surplus.while_charging_kwh, 2),
            "cloudy_kwh": round(plan.surplus.cloudy_kwh, 2),
        },
        "needed": None
        if plan.needed is None
        else {
            "at": _iso(plan.needed.at),
            "heavy_pct": plan.needed.heavy_pct,
            "typical_pct": plan.needed.typical_pct,
            "heavy_short_kwh": round(plan.needed.heavy_short_kwh, 2),
            "typical_short_kwh": round(plan.needed.typical_short_kwh, 2),
        },
        "best_window": None
        if plan.best_window is None
        else {
            "start": _iso(plan.best_window.start),
            "end": _iso(plan.best_window.end),
            "kwh": round(plan.best_window.kwh, 2),
        },
        "free_power": plan.free_power,
        "free_until": _iso(plan.free_until),
    }
    if prices is not None:
        data["costs"] = costs(plan, *prices)
    if detail:
        if night is not None:
            data["night"]["each"] = [_outcome(o) for o in night.outcomes]
            # Each replayed night's battery level, in the same order as "each".
            data["night"]["curves"] = {
                "times": [t.isoformat() for t in night.times],
                "levels": [list(row) for row in night.levels],
            }
        data["curve"] = [[t.isoformat(), round(pct, 1)] for t, pct in plan.curve]
        if plan.day is not None:
            data["day"] = {
                "pv_kwh": round(plan.day.pv_kwh, 2),
                "load_kwh": round(plan.day.load_kwh, 2),
                "reserve_at": _iso(plan.day.reserve_at),
                "curve_cloudy": [
                    [t.isoformat(), round(pct, 1)] for t, pct in plan.day.curve_cloudy
                ],
                # Half hours until sunset: start, expected solar kW, house kW.
                "slots": [[t.isoformat(), pv, load] for t, pv, load in plan.day.slots],
            }
        data["charge_limit_pct"] = plan.charge_limit_pct
    return data


def backtest_dict(cal: Calibration | None) -> dict[str, Any] | None:
    """What the recent evenings taught: the weighting, the hidden load, and how
    each evening went, so a surprise can be traced to one night."""
    if cal is None:
        return None
    contested = cal.score.contested_days
    return {
        "weighting": str(cal.weighting),
        "hidden_load_w": round(cal.standby_kw * 1000),
        "honest_error_kwh": {str(w): e for w, e in cal.honest_error_kwh.items()},
        "bias_kwh": cal.score.bias_kwh,
        "bias_without_hidden_load_kwh": cal.uncorrected.bias_kwh,
        "lasts_right": cal.score.agreement,
        "contested": len(cal.score.contested),
        "nights": [
            {
                "day": n.day.isoformat(),
                "contested": n.day in contested,
                "predicted_spare_kwh": round(n.predicted_spare_kwh, 2),
                "actual_spare_kwh": round(n.actual_spare_kwh, 2),
                "predicted_lasts": n.predicted_lasts,
                "actual_lasts": n.actual_lasts,
            }
            for n in cal.score.nights
        ],
    }
