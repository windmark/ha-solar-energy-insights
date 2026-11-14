"""The summary in Home Assistant: the plan and what the integration knows,
turned into the dashboard's rows. The wording is in core/summary.py."""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .core.changes import Change
from .core.summary import Facts, Summary, summarize
from .serialize import costs

if TYPE_CHECKING:
    from .coordinator import InsightsCoordinator
    from .core.plan import Plan

CURRENCY_SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$", "CHF": "CHF ", "SEK": "kr "}


def changed(change: Change | None, key: str) -> dict[str, Any] | None:
    """What the answer was before a recent change, since when, and why."""
    if change is None:
        return None
    was = change.was
    before: dict[str, Any] = (
        {
            "outlook": was.outlook,
            "grid_from": was.grid_from.isoformat() if was.grid_from else None,
            "spare_kwh": None if was.spare_kwh is None else round(was.spare_kwh, 1),
        }
        if key == "tonight"
        else {"full_at": was.full_at.isoformat() if was.full_at else None}
    )
    return {"was": before, "since": change.since.isoformat(), "cause": change.cause}


def facts_for(c: InsightsCoordinator, p: Plan) -> Facts:
    """Everything the rows are made from, as the sensors report it."""
    night, charge = p.night, p.charge
    battery = c.last_inputs.battery if c.last_inputs else None
    money = costs(p, c.price, c.feed_in)
    recent = c.recent_nights()
    needed = p.needed
    currency = str(c.hass.config.currency or "EUR")
    return Facts(
        now=p.now,
        tz=dt_util.get_default_time_zone(),
        sun_up=p.sun_up,
        soc=battery.soc_pct if battery else None,
        asleep="soc_pct" in c.remembered,
        limit=p.charge_limit_pct,
        outlook=None if night is None else str(night.outlook),
        replayed=None if night is None else len(night.outcomes),
        lasting=None if night is None else night.lasting,
        recent_nights=recent.get("recent_nights"),
        recent_lasted=recent.get("recent_nights_lasted"),
        budget=None if night is None else night.budget_kwh,
        evening_budget=None if night is None else night.evening_budget_kwh,
        usual_evening=None
        if night is None
        else round(float(statistics.median(o.evening_kwh for o in night.outcomes)), 2),
        evening_used=c.evening_used_kwh,
        short_evenings=()
        if night is None
        else tuple(round(o.evening_kwh, 1) for o in night.outcomes if not o.lasts),
        full_at=charge.full_at,
        full_cloudy=charge.full_at_cloudy,
        peak=round(charge.peak_pct),
        peak_cloudy=round(charge.peak_pct_cloudy),
        sunset_pct=None if charge.sunset_pct is None else round(charge.sunset_pct),
        spare=None if night is None else round(night.typical.spare_kwh, 2),
        grid_kwh=None if night is None else round(night.typical.grid_kwh, 2),
        reserve_at=None if night is None else night.typical.reserve_at,
        heavy_at=None if night is None else night.heavy.reserve_at,
        needed=None
        if needed is None
        else needed.heavy_pct
        if needed.heavy_pct is not None
        else p.charge_limit_pct,
        needed_for=None
        if needed is None
        else "sunset"
        if needed.at == p.sunset
        else "now",
        window_start=p.best_window.start if p.best_window else None,
        window_end=p.best_window.end if p.best_window else None,
        window_kwh=round(p.best_window.kwh, 2) if p.best_window else None,
        window_free=None
        if p.best_window is None or p.best_window.free_kwh is None
        else round(p.best_window.free_kwh, 2),
        surplus=round(p.surplus.total_kwh, 2),
        saving=money["surplus_saving"],
        exported=money["surplus_export_value"],
        grid_cost=money["night_grid"],
        free=p.free_power,
        free_until=p.free_until,
        changed_tonight=changed(c.changes.get("tonight"), "tonight"),
        changed_full=changed(c.changes.get("full_at"), "full_at"),
        this_morning=c.this_morning(p),
        last_night=c.last_night(),
        currency=CURRENCY_SYMBOLS.get(currency, f"{currency} "),
    )


def summary_for(c: InsightsCoordinator) -> Summary | None:
    p = c.plan
    if p is None:
        return None
    return summarize(facts_for(c, p))
