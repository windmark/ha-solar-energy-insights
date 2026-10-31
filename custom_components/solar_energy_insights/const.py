"""Constants for Solar Energy Insights."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "solar_energy_insights"

# Entities read, set in the config flow.
CONF_BATTERY_SOC: Final = "battery_soc"
CONF_BATTERY_CAPACITY: Final = "battery_capacity"
CONF_BATTERY_RESERVE: Final = "battery_reserve"
# Optional: the level charging stops at, when set below 100 % to spare the cells.
CONF_CHARGE_LIMIT: Final = "charge_limit"
CONF_HOUSE_ENERGY: Final = "house_energy"
CONF_GRID_POWER: Final = "grid_power"
CONF_SOLCAST_TODAY: Final = "solcast_today"
CONF_SOLCAST_TOMORROW: Final = "solcast_tomorrow"

# Options.
CONF_NIGHTS: Final = "nights"
CONF_MIN_RESERVE: Final = "min_reserve"
CONF_MAX_POWER: Final = "max_power"
CONF_ROUND_TRIP: Final = "round_trip"
# The charge limit when no entity gives one.
CONF_MAX_CHARGE: Final = "max_charge"
CONF_IMPORT_POSITIVE: Final = "import_positive"
CONF_PRICE: Final = "price"
CONF_FEED_IN: Final = "feed_in"
CONF_PRICE_ENTITY: Final = "price_entity"
# Optional hourly series that may tell nights apart; each is used only if it
# predicts recent evenings better.
CONF_TEMPERATURE: Final = "outdoor_temperature"
CONF_PRESENCE: Final = "presence"
# Optional: what the panels made, to learn a correction to Solcast for this roof.
CONF_SOLAR_ENERGY: Final = "solar_energy"

# The morning forecast and "full at" are kept from the first plan after sunrise,
# if it comes before this hour; the night answer from the first after this one.
MORNING_UNTIL_HOUR: Final = 11
NIGHT_ANSWER_HOUR: Final = 21
# Recent days with 5-minute statistics, for when the battery got full.
SHORT_TERM_DAYS: Final = 9

DEFAULT_NIGHTS: Final = 14
# The lowest state of charge an inverter discharges to by itself, as a floor under
# whatever reserve is set.
DEFAULT_MIN_RESERVE_PCT: Final = 5.0
DEFAULT_MAX_POWER_KW: Final = 5.0
DEFAULT_ROUND_TRIP_PCT: Final = 92.0
DEFAULT_MAX_CHARGE_PCT: Final = 100.0
# Prices in cents per kWh, until set in the options.
DEFAULT_PRICE_CT: Final = 30.0
DEFAULT_FEED_IN_CT: Final = 8.0
# Helpers whose values prefill the prices, where they exist.
PRICE_HELPER: Final = "input_number.electricity_price"
FEED_IN_HELPER: Final = "input_number.feed_in_tariff"

# Recent evenings the night answer is scored on and learned from. About a month:
# enough evenings that could have gone either way, recent enough to follow the
# season. Two months reached back into a different season, whose noise hid the
# current bias (tests/test_real_mornings.py).
BACKTEST_EVENINGS: Final = 30

# Minutes past the hour the recorder has compiled the hour before.
HISTORY_MINUTE: Final = 5
LIVE_INTERVAL_MIN: Final = 5
LIVE_COOLDOWN_S: Final = 30

EVENT_FREE_POWER: Final = f"{DOMAIN}_free_power"
