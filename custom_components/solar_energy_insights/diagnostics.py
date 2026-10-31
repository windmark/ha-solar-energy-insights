"""Diagnostics: the settings, how much history is in use, and the plan."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import InsightsConfigEntry
from .core.nights import days_covered
from .serialize import plan_dict


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: InsightsConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    current = coordinator.plan
    return {
        "entities": dict(entry.data),
        "options": dict(entry.options),
        "days_of_history": days_covered(coordinator.use),
        "weighting": str(coordinator.weighting),
        # The plan's inputs, to replay this moment as a test (tests/replay.py).
        "replay": coordinator.snapshot(),
        "backtest": {
            str(weighting): {
                "error_kwh": result.error_kwh,
                "lasts_right": result.agreement,
                "nights": [
                    {
                        "day": n.day.isoformat(),
                        "predicted_spare_kwh": round(n.predicted_spare_kwh, 2),
                        "actual_spare_kwh": round(n.actual_spare_kwh, 2),
                        "predicted_lasts": n.predicted_lasts,
                        "actual_lasts": n.actual_lasts,
                    }
                    for n in result.nights
                ],
            }
            for weighting, result in coordinator.scores.items()
        },
        "price_source": coordinator.price_source,
        "plan": plan_dict(
            current, detail=False, prices=(coordinator.price, coordinator.feed_in)
        )
        if current is not None
        else None,
    }
