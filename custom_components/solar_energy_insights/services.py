"""Actions: what-if simulations, the upgrade replay, and relearning the load."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import BACKFILL_DAYS, DOMAIN
from .coordinator import InsightsCoordinator
from .core.forecast import Quantile
from .core.load import Evening
from .core.planner import ApplianceLoad, simulate_with
from .jobs import reset_load
from .serialize import simulation_dict

SERVICE_SIMULATE = "simulate"
SERVICE_UPGRADE = "value_upgrade"
SERVICE_RELEARN = "relearn"

QUANTILES = {"expected": Quantile.P50, "cloudy": Quantile.P10, "sunny": Quantile.P90}

SIMULATE_SCHEMA = vol.Schema(
    {
        vol.Optional("appliances", default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("extra_kwh", default=0): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=50)
        ),
        vol.Optional("extra_hours", default=1): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=24)
        ),
        vol.Optional("until", default="sunrise"): vol.In(("sunrise", "sunset")),
        vol.Optional("evening", default="mix"): vol.In([str(e) for e in Evening]),
        vol.Optional("forecast", default="expected"): vol.In(tuple(QUANTILES)),
    }
)
UPGRADE_SCHEMA = vol.Schema(
    {
        vol.Required("sizes"): vol.All(
            cv.ensure_list, [vol.All(vol.Coerce(float), vol.Range(min=0.5, max=200))]
        )
    }
)
RELEARN_SCHEMA = vol.Schema(
    {
        vol.Optional("days", default=BACKFILL_DAYS): vol.All(
            int, vol.Range(min=1, max=90)
        )
    }
)


def coordinator_for(hass: HomeAssistant) -> InsightsCoordinator:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data  # type: ignore[no-any-return]
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="not_loaded"
    )


async def _simulate(call: ServiceCall) -> ServiceResponse:
    c = coordinator_for(call.hass)
    inputs = c.build_inputs()
    names = {a.name.casefold(): a for a in inputs.appliances}
    extra: list[ApplianceLoad] = []
    for name in call.data["appliances"]:
        if name.casefold() not in names:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_appliance",
                translation_placeholders={
                    "name": name,
                    "known": ", ".join(a.name for a in inputs.appliances) or "-",
                },
            )
        extra.append(names[name.casefold()])
    if call.data["extra_kwh"] > 0:
        extra.append(
            ApplianceLoad("extra", call.data["extra_kwh"], call.data["extra_hours"])
        )
    until = inputs.sunrise if call.data["until"] == "sunrise" else inputs.sunset
    sim = simulate_with(
        inputs,
        until,
        extra,
        Evening(call.data["evening"]),
        QUANTILES[call.data["forecast"]],
    )
    return simulation_dict(sim)


async def _value_upgrade(call: ServiceCall) -> ServiceResponse:
    c = coordinator_for(call.hass)
    result: dict[str, Any] = await c.learner.async_upgrade(
        sorted(set(call.data["sizes"]))
    )
    return result


async def _relearn(call: ServiceCall) -> None:
    c = coordinator_for(call.hass)
    reset_load(c.data_store)
    await c.learner.async_learn_hours(dt_util.utcnow(), limit=call.data["days"] * 24)
    await c.store.async_save()
    await c.async_request_refresh()


def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(
        DOMAIN,
        SERVICE_SIMULATE,
        _simulate,
        schema=SIMULATE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPGRADE,
        _value_upgrade,
        schema=UPGRADE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RELEARN, _relearn, schema=RELEARN_SCHEMA
    )
