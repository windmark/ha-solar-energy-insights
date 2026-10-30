"""Set up from existing entities, with a preset for EF-PowerOcean-TcpModbus.

Nothing here talks to a device: every field picks an entity. Where the PowerOcean
integration and Solcast are installed, the fields come prefilled.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CHARGE_ENERGY,
    CONF_BATTERY_DISCHARGE_ENERGY,
    CONF_BATTERY_POWER,
    CONF_BATTERY_RESERVE,
    CONF_BATTERY_SOC,
    CONF_CHARGE_POSITIVE,
    CONF_COOK_THRESHOLD,
    CONF_DEFAULT_HOURS,
    CONF_DEFAULT_KWH,
    CONF_ENERGY_ENTITY,
    CONF_FEED_IN,
    CONF_FEED_IN_ENTITY,
    CONF_GRID_EXPORT_ENERGY,
    CONF_GRID_IMPORT_ENERGY,
    CONF_GRID_POWER,
    CONF_HOUSE_ENERGY,
    CONF_HOUSE_POWER,
    CONF_IMPORT_POSITIVE,
    CONF_MAX_KWH,
    CONF_MAX_POWER,
    CONF_MIN_RESERVE,
    CONF_NAME,
    CONF_POWER_ENTITY,
    CONF_PRICE,
    CONF_PRICE_ENTITY,
    CONF_SOLAR_ENERGY,
    CONF_SOLAR_POWER,
    CONF_SOLCAST_LATER,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    CONF_STATE_ENTITY,
    CONF_UPGRADE_COST,
    CONF_UPGRADE_KWH,
    DEFAULT_COOK_THRESHOLD_KW,
    DEFAULT_FEED_IN_CT,
    DEFAULT_MAX_POWER_KW,
    DEFAULT_MIN_RESERVE_PCT,
    DEFAULT_PRICE_CT,
    DEFAULT_UPGRADE_COST_EUR,
    DEFAULT_UPGRADE_KWH,
    DOMAIN,
    SUBENTRY_APPLIANCE,
)

POWEROCEAN = "ef_powerocean_tcpmodbus"
# Our field → the PowerOcean entity key (its unique id ends in _<key>), in order of
# preference.
POWEROCEAN_KEYS: dict[str, tuple[str, ...]] = {
    CONF_BATTERY_SOC: ("battery_soc",),
    CONF_BATTERY_POWER: ("battery_power",),
    CONF_BATTERY_CAPACITY: ("battery_capacity",),
    CONF_BATTERY_RESERVE: ("battery_reserve_effective", "min_soc_limit"),
    CONF_SOLAR_POWER: ("solar_power",),
    CONF_HOUSE_POWER: ("house_power",),
    CONF_GRID_POWER: ("grid_power",),
    CONF_SOLAR_ENERGY: ("solar_total",),
    CONF_HOUSE_ENERGY: ("house_energy_total",),
    CONF_GRID_IMPORT_ENERGY: ("grid_import_total",),
    CONF_GRID_EXPORT_ENERGY: ("grid_export_total",),
    CONF_BATTERY_CHARGE_ENERGY: ("bat_charged_total",),
    CONF_BATTERY_DISCHARGE_ENERGY: ("bat_discharged_total",),
}
SOLCAST_IDS = {
    CONF_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
    CONF_SOLCAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
}
SOLCAST_LATER_IDS = tuple(
    f"sensor.solcast_pv_forecast_forecast_day_{n}" for n in range(3, 8)
)


def _sensor(
    device_class: SensorDeviceClass | None = None, multiple: bool = False
) -> EntitySelector:
    config = EntitySelectorConfig(domain="sensor", multiple=multiple)
    if device_class:
        config = EntitySelectorConfig(
            domain="sensor", device_class=str(device_class), multiple=multiple
        )
    return EntitySelector(config)


def _number(minimum: float, maximum: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.BOX,
        )
    )


BATTERY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BATTERY_SOC): _sensor(SensorDeviceClass.BATTERY),
        vol.Required(CONF_BATTERY_POWER): _sensor(SensorDeviceClass.POWER),
        vol.Required(CONF_BATTERY_CAPACITY): _sensor(),
        vol.Optional(CONF_BATTERY_RESERVE): _sensor(),
        vol.Required(CONF_SOLAR_POWER): _sensor(SensorDeviceClass.POWER),
        vol.Required(CONF_HOUSE_POWER): _sensor(SensorDeviceClass.POWER),
        vol.Required(CONF_GRID_POWER): _sensor(SensorDeviceClass.POWER),
    }
)
ENERGY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SOLAR_ENERGY): _sensor(SensorDeviceClass.ENERGY),
        vol.Required(CONF_HOUSE_ENERGY): _sensor(SensorDeviceClass.ENERGY),
        vol.Required(CONF_GRID_IMPORT_ENERGY): _sensor(SensorDeviceClass.ENERGY),
        vol.Required(CONF_GRID_EXPORT_ENERGY): _sensor(SensorDeviceClass.ENERGY),
        vol.Required(CONF_BATTERY_CHARGE_ENERGY): _sensor(SensorDeviceClass.ENERGY),
        vol.Required(CONF_BATTERY_DISCHARGE_ENERGY): _sensor(SensorDeviceClass.ENERGY),
    }
)
FORECAST_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SOLCAST_TODAY): _sensor(),
        vol.Required(CONF_SOLCAST_TOMORROW): _sensor(),
        vol.Optional(CONF_SOLCAST_LATER): _sensor(multiple=True),
    }
)


def suggested_entities(hass: HomeAssistant) -> dict[str, Any]:
    """Prefill from EF-PowerOcean-TcpModbus and Solcast where they are installed."""
    registry = er.async_get(hass)
    by_key: dict[str, str] = {}
    for entry in registry.entities.values():
        if entry.platform == POWEROCEAN and entry.domain == "sensor":
            # Keys hold underscores, so match each known key as a suffix.
            for keys in POWEROCEAN_KEYS.values():
                for key in keys:
                    if entry.unique_id.endswith(f"_{key}"):
                        by_key[key] = entry.entity_id
    out: dict[str, Any] = {
        field: next(by_key[key] for key in keys if key in by_key)
        for field, keys in POWEROCEAN_KEYS.items()
        if any(key in by_key for key in keys)
    }
    out.update(
        {
            field: entity_id
            for field, entity_id in SOLCAST_IDS.items()
            if hass.states.get(entity_id)
        }
    )
    later = [entity_id for entity_id in SOLCAST_LATER_IDS if hass.states.get(entity_id)]
    if later:
        out[CONF_SOLCAST_LATER] = later
    return out


class InsightsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_energy()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                BATTERY_SCHEMA, suggested_entities(self.hass)
            ),
        )

    async def async_step_energy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_forecast()
        return self.async_show_form(
            step_id="energy",
            data_schema=self.add_suggested_values_to_schema(
                ENERGY_SCHEMA, suggested_entities(self.hass)
            ),
        )

    async def async_step_forecast(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            for field in (CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW):
                state = self.hass.states.get(user_input[field])
                if state is None or not state.attributes.get("detailedForecast"):
                    errors[field] = "not_solcast"
            if not errors:
                self._data.update(user_input)
                return self.async_create_entry(
                    title="Solar Energy Insights", data=self._data
                )
        return self.async_show_form(
            step_id="forecast",
            data_schema=self.add_suggested_values_to_schema(
                FORECAST_SCHEMA, user_input or suggested_entities(self.hass)
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        schema = vol.Schema(
            {**BATTERY_SCHEMA.schema, **ENERGY_SCHEMA.schema, **FORECAST_SCHEMA.schema}
        )
        if user_input is not None:
            return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, dict(entry.data)),
        )

    @staticmethod
    @callback
    def async_get_options_flow(_config_entry: ConfigEntry) -> InsightsOptionsFlow:
        return InsightsOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, _config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_APPLIANCE: ApplianceSubentryFlow}


OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PRICE, default=DEFAULT_PRICE_CT): _number(
            0, 200, 0.1, "ct/kWh"
        ),
        vol.Required(CONF_FEED_IN, default=DEFAULT_FEED_IN_CT): _number(
            0, 100, 0.1, "ct/kWh"
        ),
        vol.Optional(CONF_PRICE_ENTITY): EntitySelector(
            EntitySelectorConfig(domain=["sensor", "input_number"])
        ),
        vol.Optional(CONF_FEED_IN_ENTITY): EntitySelector(
            EntitySelectorConfig(domain=["sensor", "input_number"])
        ),
        vol.Required(CONF_MIN_RESERVE, default=DEFAULT_MIN_RESERVE_PCT): _number(
            0, 50, 1, "%"
        ),
        vol.Required(CONF_MAX_POWER, default=DEFAULT_MAX_POWER_KW): _number(
            0.5, 50, 0.1, "kW"
        ),
        vol.Required(CONF_CHARGE_POSITIVE, default=True): BooleanSelector(),
        vol.Required(CONF_IMPORT_POSITIVE, default=True): BooleanSelector(),
        vol.Required(CONF_COOK_THRESHOLD, default=DEFAULT_COOK_THRESHOLD_KW): _number(
            0.5, 10, 0.1, "kW"
        ),
        vol.Required(CONF_UPGRADE_KWH, default=DEFAULT_UPGRADE_KWH): _number(
            0.5, 100, 0.5, "kWh"
        ),
        vol.Required(CONF_UPGRADE_COST, default=DEFAULT_UPGRADE_COST_EUR): _number(
            0, 100000, 10, "EUR"
        ),
    }
)


class InsightsOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )


APPLIANCE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): TextSelector(),
        vol.Optional(CONF_POWER_ENTITY): _sensor(SensorDeviceClass.POWER),
        vol.Optional(CONF_ENERGY_ENTITY): _sensor(SensorDeviceClass.ENERGY),
        vol.Optional(CONF_STATE_ENTITY): EntitySelector(EntitySelectorConfig()),
        vol.Required(CONF_DEFAULT_KWH, default=1.0): _number(0.05, 20, 0.05, "kWh"),
        vol.Required(CONF_DEFAULT_HOURS, default=2.0): _number(0.25, 8, 0.25, "h"),
        vol.Required(CONF_MAX_KWH, default=6.0): _number(0.1, 50, 0.1, "kWh"),
    }
)


def _appliance_errors(user_input: dict[str, Any]) -> dict[str, str]:
    if not (user_input.get(CONF_POWER_ENTITY) or user_input.get(CONF_STATE_ENTITY)):
        return {"base": "appliance_needs_input"}
    return {}


class ApplianceSubentryFlow(ConfigSubentryFlow):
    """An appliance: a plug with a power (and energy) meter, or just a state."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _appliance_errors(user_input)
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                APPLIANCE_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _appliance_errors(user_input)
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=user_input[CONF_NAME],
                    data=user_input,
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                APPLIANCE_SCHEMA, user_input or dict(subentry.data)
            ),
            errors=errors,
        )
