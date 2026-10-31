"""The simulate action: the same plan, with an extra load started now."""

from __future__ import annotations

from datetime import datetime

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import InsightsCoordinator
from .core.battery import Rate
from .core.plan import plan
from .serialize import plan_dict

SIMULATE_SCHEMA = vol.Schema(
    {
        vol.Required("extra_kwh"): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional("extra_hours", default=1.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=24)
        ),
    }
)


def coordinator_for(hass: HomeAssistant) -> InsightsCoordinator:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            coordinator: InsightsCoordinator = entry.runtime_data
            return coordinator
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="not_loaded"
    )


def _extra(start: datetime, kwh: float, hours: float) -> Rate:
    def kw(t: datetime) -> float:
        elapsed = (t - start).total_seconds() / 3600
        return kwh / hours if 0 <= elapsed < hours else 0.0

    return kw


async def _simulate(call: ServiceCall) -> ServiceResponse:
    coordinator = coordinator_for(call.hass)
    now = dt_util.utcnow()
    extra = _extra(now, call.data["extra_kwh"], call.data["extra_hours"])
    try:
        inputs = coordinator.inputs(extra, now)
    except UpdateFailed as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_ready",
            translation_placeholders={"reason": str(err)},
        ) from err
    return plan_dict(plan(inputs), prices=(coordinator.price, coordinator.feed_in))


def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(
        DOMAIN,
        "simulate",
        _simulate,
        schema=SIMULATE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
