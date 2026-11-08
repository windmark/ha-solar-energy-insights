"""Set up from existing entities, prefilled for EF-PowerOcean-TcpModbus and Solcast."""

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
from homeassistant.const import CONF_ICON, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    IconSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .array import array_of
from .const import (
    CONF_ACHIEVEMENTS,
    CONF_ARRAY_KWP,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_RESERVE,
    CONF_BATTERY_SOC,
    CONF_CHARGE_LIMIT,
    CONF_ENERGY_PER_RUN,
    CONF_FEED_IN,
    CONF_FEED_IN_CAP,
    CONF_FEED_IN_ENTITY,
    CONF_GRID_EXPORT_ENERGY,
    CONF_GRID_IMPORT_ENERGY,
    CONF_GRID_POWER,
    CONF_HOUSE_ENERGY,
    CONF_IMPORT_POSITIVE,
    CONF_LOOKBACK,
    CONF_MAX_CHARGE,
    CONF_MAX_POWER,
    CONF_MIN_RESERVE,
    CONF_NIGHTS,
    CONF_PRICE,
    CONF_PRICE_ENTITY,
    CONF_ROUND_TRIP,
    CONF_RUN_HOURS,
    CONF_RUN_KWH,
    CONF_SOLAR_ENERGY,
    CONF_SOLAR_POWER,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    DEFAULT_FEED_IN_CT,
    DEFAULT_LOOKBACK_MIN,
    DEFAULT_MAX_CHARGE_PCT,
    DEFAULT_MAX_POWER_KW,
    DEFAULT_MIN_RESERVE_PCT,
    DEFAULT_NIGHTS,
    DEFAULT_PRICE_CT,
    DEFAULT_ROUND_TRIP_PCT,
    DEFAULT_RUN_HOURS,
    DEFAULT_RUN_KWH,
    DOMAIN,
    SUBENTRY_APPLIANCE,
)
from .dashboard import (
    discover_strings,
    roofs,
    string_panel_w_key,
    string_panels_key,
    string_roof_key,
)

POWEROCEAN = "ef_powerocean_tcpmodbus"
# PowerOcean unique ids end in _<key>; preferred first.
POWEROCEAN_KEYS: dict[str, tuple[str, ...]] = {
    CONF_BATTERY_SOC: ("battery_soc",),
    CONF_BATTERY_CAPACITY: ("battery_capacity",),
    CONF_BATTERY_RESERVE: ("battery_reserve_effective", "min_soc_limit"),
    # A number entity: the limit set while Modbus control is on.
    CONF_CHARGE_LIMIT: ("charge_limit_soc",),
    CONF_HOUSE_ENERGY: ("house_energy_total",),
    CONF_GRID_POWER: ("grid_power",),
}
SOLCAST_IDS = {
    CONF_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
    CONF_SOLCAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
}


def _sensor(device_class: SensorDeviceClass | None = None) -> EntitySelector:
    if device_class is None:
        return EntitySelector(EntitySelectorConfig(domain="sensor"))
    return EntitySelector(
        EntitySelectorConfig(domain="sensor", device_class=str(device_class))
    )


def _number(low: float, high: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=low,
            max=high,
            step=step,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.BOX,
        )
    )


SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BATTERY_SOC): _sensor(SensorDeviceClass.BATTERY),
        vol.Required(CONF_BATTERY_CAPACITY): _sensor(),
        vol.Optional(CONF_BATTERY_RESERVE): _sensor(),
        vol.Optional(CONF_CHARGE_LIMIT): EntitySelector(
            EntitySelectorConfig(domain=["number", "sensor", "input_number"])
        ),
        vol.Required(CONF_HOUSE_ENERGY): _sensor(SensorDeviceClass.ENERGY),
        vol.Optional(CONF_GRID_POWER): _sensor(SensorDeviceClass.POWER),
        vol.Required(CONF_SOLCAST_TODAY): _sensor(),
        vol.Required(CONF_SOLCAST_TOMORROW): _sensor(),
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PRICE, default=DEFAULT_PRICE_CT): _number(
            0, 500, 0.01, "ct/kWh"
        ),
        vol.Required(CONF_FEED_IN, default=DEFAULT_FEED_IN_CT): _number(
            0, 500, 0.01, "ct/kWh"
        ),
        vol.Optional(CONF_PRICE_ENTITY): EntitySelector(
            EntitySelectorConfig(domain=["sensor", "input_number"])
        ),
        vol.Optional(CONF_FEED_IN_ENTITY): EntitySelector(
            EntitySelectorConfig(domain=["sensor", "input_number"])
        ),
        vol.Optional(CONF_SOLAR_ENERGY): _sensor(SensorDeviceClass.ENERGY),
        vol.Required(CONF_LOOKBACK, default=DEFAULT_LOOKBACK_MIN): _number(
            30, 120, 15, "min"
        ),
        vol.Required(CONF_ACHIEVEMENTS, default=False): BooleanSelector(),
        vol.Optional(CONF_GRID_IMPORT_ENERGY): _sensor(SensorDeviceClass.ENERGY),
        vol.Optional(CONF_GRID_EXPORT_ENERGY): _sensor(SensorDeviceClass.ENERGY),
        vol.Optional(CONF_SOLAR_POWER): _sensor(SensorDeviceClass.POWER),
        vol.Optional(CONF_FEED_IN_CAP): EntitySelector(
            EntitySelectorConfig(domain=["sensor", "number", "input_number"])
        ),
        vol.Optional(CONF_ARRAY_KWP): _number(0.1, 1000, 0.01, "kWp"),
        vol.Required(CONF_NIGHTS, default=DEFAULT_NIGHTS): _number(3, 60, 1, "nights"),
        vol.Required(CONF_MIN_RESERVE, default=DEFAULT_MIN_RESERVE_PCT): _number(
            0, 50, 1, "%"
        ),
        vol.Required(CONF_MAX_POWER, default=DEFAULT_MAX_POWER_KW): _number(
            0.5, 50, 0.1, "kW"
        ),
        vol.Required(CONF_MAX_CHARGE, default=DEFAULT_MAX_CHARGE_PCT): _number(
            50, 100, 1, "%"
        ),
        vol.Required(CONF_ROUND_TRIP, default=DEFAULT_ROUND_TRIP_PCT): _number(
            70, 100, 1, "%"
        ),
        vol.Required(CONF_IMPORT_POSITIVE, default=True): BooleanSelector(),
    }
)


POWEROCEAN_OPTION_KEYS: dict[str, tuple[str, ...]] = {
    CONF_SOLAR_ENERGY: ("solar_total",),
    CONF_GRID_IMPORT_ENERGY: ("grid_import_total",),
    CONF_GRID_EXPORT_ENERGY: ("grid_export_total",),
    CONF_SOLAR_POWER: ("solar_power",),
    # The cap in force, which is where the inverter curtails.
    CONF_FEED_IN_CAP: ("feed_in_power_max",),
}


def _array_size(hass: HomeAssistant) -> dict[str, float]:
    """The array's DC size from Solcast's rooftop sites."""
    kwp = array_of(hass).kwp
    return {CONF_ARRAY_KWP: round(kwp, 2)} if kwp else {}


def _powerocean(
    hass: HomeAssistant, keys: dict[str, tuple[str, ...]]
) -> dict[str, str]:
    """Entities of EF-PowerOcean-TcpModbus for these fields, by unique id."""
    found: dict[str, str] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform != POWEROCEAN or entry.domain not in ("sensor", "number"):
            continue
        for options in keys.values():
            for key in options:
                if entry.unique_id.endswith(f"_{key}"):
                    found[key] = entry.entity_id
    return {
        field: next(found[key] for key in options if key in found)
        for field, options in keys.items()
        if any(key in found for key in options)
    }


def suggested_entities(hass: HomeAssistant) -> dict[str, Any]:
    """Prefill from EF-PowerOcean-TcpModbus and Solcast where they are installed."""
    found: dict[str, str] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform != POWEROCEAN or entry.domain not in ("sensor", "number"):
            continue
        for keys in POWEROCEAN_KEYS.values():
            for key in keys:
                if entry.unique_id.endswith(f"_{key}"):
                    found[key] = entry.entity_id
    out: dict[str, Any] = {
        field: next(found[key] for key in keys if key in found)
        for field, keys in POWEROCEAN_KEYS.items()
        if any(key in found for key in keys)
    }
    out.update({field: e for field, e in SOLCAST_IDS.items() if hass.states.get(e)})
    return out


class InsightsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            for field in (CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW):
                state = self.hass.states.get(user_input[field])
                if state is None or not state.attributes.get("detailedForecast"):
                    errors[field] = "not_solcast"
            if not errors:
                return self.async_create_entry(
                    title="Solar Energy Insights", data=user_input
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                SCHEMA, user_input or suggested_entities(self.hass)
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(SCHEMA, dict(entry.data)),
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
        return {SUBENTRY_APPLIANCE: ApplianceFlow}


def _slider(low: float, high: float, step: float, unit: str) -> NumberSelector:
    # Sliders, not boxes: some phone keypads can't type a decimal point.
    return NumberSelector(
        NumberSelectorConfig(
            min=low,
            max=high,
            step=step,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.SLIDER,
        )
    )


APPLIANCE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): TextSelector(),
        vol.Optional(CONF_ICON): IconSelector(),
        vol.Required(CONF_RUN_KWH, default=DEFAULT_RUN_KWH): _slider(
            0.1, 10, 0.1, "kWh"
        ),
        vol.Required(CONF_RUN_HOURS, default=DEFAULT_RUN_HOURS): _slider(
            0.5, 8, 0.5, "h"
        ),
        vol.Optional(CONF_ENERGY_PER_RUN): _sensor(),
    }
)


class ApplianceFlow(ConfigSubentryFlow):
    """An appliance to start in the sun: its name, and what a run takes."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=APPLIANCE_SCHEMA)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=user_input[CONF_NAME],
                data=user_input,
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                APPLIANCE_SCHEMA, dict(subentry.data)
            ),
        )


class InsightsOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        strings = discover_strings(self.hass, self.config_entry)
        roof_choices = [
            SelectOptionDict(
                value=r["name"],
                label=(
                    f"{r['name']} (facing {r['facing']}, "
                    f"{r['tilt']:.0f}° tilt, {r['kwp']:g} kWp)"
                ),
            )
            for r in roofs(self.hass)
        ]
        fields: dict[Any, Any] = {}
        for st in strings:
            fields[vol.Optional(string_panels_key(st["n"]))] = _number(
                1, 200, 1, "panels"
            )
            fields[vol.Optional(string_panel_w_key(st["n"]))] = _number(
                50, 1000, 1, "W"
            )
            if roof_choices:
                fields[vol.Optional(string_roof_key(st["n"]))] = SelectSelector(
                    SelectSelectorConfig(
                        options=roof_choices, mode=SelectSelectorMode.DROPDOWN
                    )
                )
        schema = OPTIONS_SCHEMA.extend(fields)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    **_powerocean(self.hass, POWEROCEAN_OPTION_KEYS),
                    **_array_size(self.hass),
                    **self.config_entry.options,
                },
            ),
        )
