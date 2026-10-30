"""Constants for Solar Energy Insights."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "solar_energy_insights"

# Entities read, set in the config flow.
CONF_BATTERY_SOC: Final = "battery_soc"
CONF_BATTERY_CAPACITY: Final = "battery_capacity"
CONF_BATTERY_RESERVE: Final = "battery_reserve"
CONF_HOUSE_ENERGY: Final = "house_energy"
CONF_GRID_POWER: Final = "grid_power"
CONF_SOLCAST_TODAY: Final = "solcast_today"
CONF_SOLCAST_TOMORROW: Final = "solcast_tomorrow"

# Options.
CONF_NIGHTS: Final = "nights"
CONF_MIN_RESERVE: Final = "min_reserve"
CONF_MAX_POWER: Final = "max_power"
CONF_ROUND_TRIP: Final = "round_trip"
CONF_IMPORT_POSITIVE: Final = "import_positive"

DEFAULT_NIGHTS: Final = 14
# The lowest state of charge an inverter discharges to by itself, as a floor under
# whatever reserve is set.
DEFAULT_MIN_RESERVE_PCT: Final = 5.0
DEFAULT_MAX_POWER_KW: Final = 5.0
DEFAULT_ROUND_TRIP_PCT: Final = 92.0

# Recent evenings the night answer is scored on, to choose how nights are weighted.
BACKTEST_EVENINGS: Final = 14

# Minutes past the hour the recorder has compiled the hour before.
HISTORY_MINUTE: Final = 5
LIVE_INTERVAL_MIN: Final = 5
LIVE_COOLDOWN_S: Final = 30

EVENT_FREE_POWER: Final = f"{DOMAIN}_free_power"
