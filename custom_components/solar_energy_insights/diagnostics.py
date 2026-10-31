"""Diagnostics: the settings, how much history is in use, and the plan."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from .coordinator import InsightsConfigEntry
from .core.nights import days_covered
from .serialize import backtest_dict, plan_dict

if TYPE_CHECKING:
    from .coordinator import InsightsCoordinator


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
        "backtest": backtest_dict(coordinator.calibration),
        "corrected_readings": coordinator.corrected_readings,
        "heavy_share": coordinator.heavy_share,
        "read_from_devices": _devices(coordinator),
        "achievements": None
        if not coordinator.achievements.enabled
        else {
            "days": len(coordinator.memory.memory.facts),
            "quests": len(coordinator.memory.memory.quests),
            "view": coordinator.achievements.view,
        },
        "memory": {
            "weighting": coordinator.memory.memory.weighting,
            "mornings": len(coordinator.memory.memory.forecasts),
            "night_answers": len(coordinator.memory.memory.nights),
        },
        "live": {
            "nights": None
            if coordinator.live_nights is None
            else {
                "count": coordinator.live_nights.nights,
                "error_kwh": coordinator.live_nights.error_kwh,
                "lasts_right": coordinator.live_nights.lasts_right,
            },
            "full_at": None
            if coordinator.live_full is None
            else {
                "days": coordinator.live_full.days,
                "error_minutes": coordinator.live_full.error_minutes,
                "missed": coordinator.live_full.missed,
            },
        },
        "forecast_correction": {
            "active": coordinator.correction.active,
            "days": coordinator.correction.days,
            "raw_error_kwh": coordinator.correction.raw_error_kwh,
            "corrected_error_kwh": coordinator.correction.corrected_error_kwh,
            "factors": [float(f) for f in coordinator.correction.factors],
        },
        "price_source": coordinator.price_source,
        "plan": plan_dict(
            current, detail=False, prices=(coordinator.price, coordinator.feed_in)
        )
        if current is not None
        else None,
    }


def _devices(coordinator: InsightsCoordinator) -> dict[str, Any]:
    """What was read from the battery and Solcast, as the plan uses it."""
    array = coordinator.array
    try:
        battery = coordinator.battery()
        limits = {
            "charge_kw": battery.max_power_kw,
            "discharge_kw": battery.discharge_kw,
        }
    except UpdateFailed:
        limits = {}
    return {
        "array": {
            "kwp": array.kwp,
            "relative_yield": round(array.relative_yield, 3),
            "planes": [
                {"kwp": p.kwp, "compass": p.compass_deg, "tilt": p.tilt_deg}
                for p in array.planes
            ],
        },
        "battery_health": round(coordinator.health, 3),
        **limits,
    }
