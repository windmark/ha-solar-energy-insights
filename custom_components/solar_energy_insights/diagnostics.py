"""Diagnostics: settings and the state of what has been learned, no history."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import InsightsConfigEntry
from .serialize import plan_dict


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: InsightsConfigEntry
) -> dict[str, Any]:
    c = entry.runtime_data
    data = c.data_store
    return {
        "entities": dict(entry.data),
        "options": dict(entry.options),
        "appliances": [
            {
                "name": a.name,
                "metered": a.metered,
                "learned_runs": len(c.profile(a).runs),
            }
            for a in c.appliances
        ],
        "learned": {
            "records": len(data.records),
            "first_day": min(data.records, default=None),
            "last_hour": data.last_hour,
            "load_mae_kwh": data.load.mae_kwh,
            "hours_scored": data.load.scored_hours,
            "evenings": {"cook": data.load.n_cook, "quiet": data.load.n_quiet},
            "forecast_correction": {
                "active": data.pv_bias.active,
                "days_scored": data.pv_bias.scored_days,
                "improvement_pct": data.pv_bias.improvement_pct,
            },
            "capacity_kwh": data.capacity_kwh,
            "round_trip": c.round_trip,
            "upgrade": data.upgrade,
        },
        "plan": plan_dict(plan, curve=False) if (plan := c.plan) is not None else None,
    }
