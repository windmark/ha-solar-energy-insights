"""Constants for Solar Energy Insights."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "solar_energy_insights"

# ── Entities read, set in the config flow ──
CONF_BATTERY_SOC: Final = "battery_soc"
CONF_BATTERY_POWER: Final = "battery_power"
CONF_BATTERY_CAPACITY: Final = "battery_capacity"
CONF_BATTERY_RESERVE: Final = "battery_reserve"
CONF_SOLAR_POWER: Final = "solar_power"
CONF_HOUSE_POWER: Final = "house_power"
CONF_GRID_POWER: Final = "grid_power"
CONF_SOLAR_ENERGY: Final = "solar_energy"
CONF_HOUSE_ENERGY: Final = "house_energy"
CONF_GRID_IMPORT_ENERGY: Final = "grid_import_energy"
CONF_GRID_EXPORT_ENERGY: Final = "grid_export_energy"
CONF_BATTERY_CHARGE_ENERGY: Final = "battery_charge_energy"
CONF_BATTERY_DISCHARGE_ENERGY: Final = "battery_discharge_energy"
CONF_SOLCAST_TODAY: Final = "solcast_today"
CONF_SOLCAST_TOMORROW: Final = "solcast_tomorrow"
CONF_SOLCAST_LATER: Final = "solcast_later"

POWER_KEYS: Final = (
    CONF_BATTERY_POWER,
    CONF_SOLAR_POWER,
    CONF_HOUSE_POWER,
    CONF_GRID_POWER,
)
ENERGY_KEYS: Final = (
    CONF_SOLAR_ENERGY,
    CONF_HOUSE_ENERGY,
    CONF_GRID_IMPORT_ENERGY,
    CONF_GRID_EXPORT_ENERGY,
    CONF_BATTERY_CHARGE_ENERGY,
    CONF_BATTERY_DISCHARGE_ENERGY,
)

# ── Options ──
CONF_PRICE: Final = "price"
CONF_FEED_IN: Final = "feed_in"
CONF_PRICE_ENTITY: Final = "price_entity"
CONF_FEED_IN_ENTITY: Final = "feed_in_entity"
CONF_MIN_RESERVE: Final = "min_reserve"
CONF_MAX_POWER: Final = "max_power"
CONF_CHARGE_POSITIVE: Final = "charge_positive"
CONF_IMPORT_POSITIVE: Final = "import_positive"
CONF_COOK_THRESHOLD: Final = "cook_threshold"
CONF_UPGRADE_KWH: Final = "upgrade_kwh"
CONF_UPGRADE_COST: Final = "upgrade_cost"

DEFAULT_PRICE_CT: Final = 32.0
DEFAULT_FEED_IN_CT: Final = 7.9
# The lowest state of charge an inverter discharges to by itself, as a floor under
# whatever reserve is set.
DEFAULT_MIN_RESERVE_PCT: Final = 5.0
DEFAULT_MAX_POWER_KW: Final = 5.0
DEFAULT_COOK_THRESHOLD_KW: Final = 2.0
DEFAULT_UPGRADE_KWH: Final = 8.0
DEFAULT_UPGRADE_COST_EUR: Final = 1800.0
DEFAULT_ROUND_TRIP: Final = 0.92

# ── Appliance subentries ──
SUBENTRY_APPLIANCE: Final = "appliance"
CONF_NAME: Final = "name"
CONF_POWER_ENTITY: Final = "power_entity"
CONF_ENERGY_ENTITY: Final = "energy_entity"
CONF_STATE_ENTITY: Final = "state_entity"
CONF_DEFAULT_KWH: Final = "default_kwh"
CONF_DEFAULT_HOURS: Final = "default_hours"
CONF_MAX_KWH: Final = "max_kwh"
# States of an unmetered appliance's state entity that mean it is running.
RUNNING_STATES: Final = frozenset(
    {
        "run",
        "running",
        "active",
        "on",
        "starting",
        "washing",
        "drying",
        "rinsing",
        "spinning",
        "heating",
        "cooling",
        "anti_wrinkle",
    }
)

# ── Scheduling ──
# Minutes past the hour the hourly job runs, once the recorder has compiled it.
HOURLY_MINUTE: Final = 5
DAILY_TIME: Final = (0, 15)
SNAPSHOT_MORNING: Final = (6, 0)
SNAPSHOT_MIDDAY: Final = (12, 0)
SNAPSHOT_DAY_AHEAD: Final = (21, 0)
SNAPSHOT_FINAL: Final = (23, 55)
# Hours the hourly job catches up after a restart, at most.
CATCH_UP_HOURS: Final = 48
# Days rebuilt from statistics on first setup.
BACKFILL_DAYS: Final = 30
KEEP_DAYS: Final = 400
LIVE_COOLDOWN_S: Final = 30
LIVE_INTERVAL_MIN: Final = 5

EVENT_FREE_POWER: Final = f"{DOMAIN}_free_power"
