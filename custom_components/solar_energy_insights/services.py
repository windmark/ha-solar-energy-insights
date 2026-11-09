"""The simulate action: the same plan, with an extra load started now; and the
scenario action: the last year again, with a bigger battery or a heat pump."""

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
from .core.scenario import HeatPump, Scenario, compare
from .scenario_lab import comparison_dict
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


# Too few days say nothing about a year.
MIN_SCENARIO_DAYS = 7

SCENARIO_SCHEMA = vol.Schema(
    {
        vol.Optional("extra_battery_kwh", default=0.0): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional("heat_loss_w_per_k", default=0.0): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=2000)
        ),
        vol.Optional("heating_limit_c", default=15.0): vol.All(
            vol.Coerce(float), vol.Range(min=5, max=22)
        ),
        vol.Optional("indoor_c", default=20.0): vol.All(
            vol.Coerce(float), vol.Range(min=15, max=25)
        ),
        vol.Optional("hot_water_kwh_per_day", default=0.0): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=50)
        ),
        vol.Optional("battery_price"): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=1_000_000)
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


def _refuse(key: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


async def _scenario(call: ServiceCall) -> ServiceResponse:
    coordinator = coordinator_for(call.hass)
    data = call.data
    pump = (
        HeatPump(
            heat_loss_w_per_k=data["heat_loss_w_per_k"],
            heating_limit_c=data["heating_limit_c"],
            indoor_c=data["indoor_c"],
            hot_water_kwh_per_day=data["hot_water_kwh_per_day"],
        )
        if data["heat_loss_w_per_k"] > 0 or data["hot_water_kwh_per_day"] > 0
        else None
    )
    lab = coordinator.scenario_lab
    if lab.solar is None:
        raise _refuse("needs_solar_counter")
    if pump is not None and lab.temperature is None:
        raise _refuse("needs_temperature")
    try:
        battery = coordinator.battery(as_reported=True)
    except UpdateFailed as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_ready",
            translation_placeholders={"reason": str(err)},
        ) from err
    hours = await lab.async_hours()
    if len(hours) < MIN_SCENARIO_DAYS * 24:
        raise _refuse("too_little_history")
    scenario = Scenario(extra_battery_kwh=data["extra_battery_kwh"], heat_pump=pump)
    try:
        result = await call.hass.async_add_executor_job(
            compare, hours, battery, scenario, coordinator.price, coordinator.feed_in
        )
    except ValueError as err:
        raise _refuse("needs_temperature") from err
    return comparison_dict(result, data["extra_battery_kwh"], data.get("battery_price"))


def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(
        DOMAIN,
        "simulate",
        _simulate,
        schema=SIMULATE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "scenario",
        _scenario,
        schema=SCENARIO_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
