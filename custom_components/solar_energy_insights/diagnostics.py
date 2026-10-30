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
        "plan": plan_dict(current, detail=False) if current is not None else None,
    }
