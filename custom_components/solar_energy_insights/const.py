"""Constants for Solar Energy Insights."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "solar_energy_insights"

CONF_BATTERY_SOC: Final = "battery_soc"
CONF_BATTERY_CAPACITY: Final = "battery_capacity"
CONF_BATTERY_RESERVE: Final = "battery_reserve"
CONF_CHARGE_LIMIT: Final = "charge_limit"
CONF_HOUSE_ENERGY: Final = "house_energy"
CONF_GRID_POWER: Final = "grid_power"
CONF_SOLCAST_TODAY: Final = "solcast_today"
CONF_SOLCAST_TOMORROW: Final = "solcast_tomorrow"

CONF_NIGHTS: Final = "nights"
CONF_MIN_RESERVE: Final = "min_reserve"
CONF_MAX_POWER: Final = "max_power"
CONF_ROUND_TRIP: Final = "round_trip"
CONF_MAX_CHARGE: Final = "max_charge"
CONF_IMPORT_POSITIVE: Final = "import_positive"
CONF_PRICE: Final = "price"
CONF_FEED_IN: Final = "feed_in"
CONF_PRICE_ENTITY: Final = "price_entity"
CONF_FEED_IN_ENTITY: Final = "feed_in_entity"
# Learns a correction to Solcast for this roof.
CONF_SOLAR_ENERGY: Final = "solar_energy"

CONF_ACHIEVEMENTS: Final = "achievements"
CONF_LOOKBACK: Final = "lookback"
DEFAULT_LOOKBACK_MIN: Final = 30
CONF_GRID_IMPORT_ENERGY: Final = "grid_import_energy"
CONF_GRID_EXPORT_ENERGY: Final = "grid_export_energy"
CONF_SOLAR_POWER: Final = "solar_power"
# The most the grid takes: export above it is curtailed.
CONF_FEED_IN_CAP: Final = "feed_in_cap"
# For when Solcast's sites don't say the array's size.
CONF_ARRAY_KWP: Final = "array_kwp"
EVENT_ACHIEVEMENT: Final = f"{DOMAIN}_achievement"
BACKFILL_DAYS: Final = 400
# Closed days are rebuilt this far back each day: last night ends this morning.
REBUILD_DAYS: Final = 3
QUEST_HOUR: Final = 20

# Morning answers are kept from the first plan after sunrise if it comes before
# this hour; the night answer from the first plan after it.
MORNING_UNTIL_HOUR: Final = 11
NIGHT_ANSWER_HOUR: Final = 21
# How long the recorder keeps 5-minute statistics, by default.
SHORT_TERM_DAYS: Final = 9

DEFAULT_NIGHTS: Final = 14
# Inverters discharge no lower than this by themselves, whatever reserve is set.
DEFAULT_MIN_RESERVE_PCT: Final = 5.0
DEFAULT_MAX_POWER_KW: Final = 5.0
DEFAULT_ROUND_TRIP_PCT: Final = 92.0
DEFAULT_MAX_CHARGE_PCT: Final = 100.0
DEFAULT_PRICE_CT: Final = 30.0
DEFAULT_FEED_IN_CT: Final = 8.0

# About a month: enough contested evenings, yet recent enough to follow the
# season. Two months reached into a different season whose noise hid the
# current bias (tests/test_real_mornings.py).
BACKTEST_EVENINGS: Final = 30

# By then the recorder has compiled the hour before.
HISTORY_MINUTE: Final = 5
LIVE_INTERVAL_MIN: Final = 5
LIVE_COOLDOWN_S: Final = 30

EVENT_FREE_POWER: Final = f"{DOMAIN}_free_power"

# An appliance to start in the sun: one subentry each.
SUBENTRY_APPLIANCE: Final = "appliance"
CONF_RUN_KWH: Final = "kwh"
CONF_RUN_HOURS: Final = "hours"
# A sensor that learns the energy of the last run, and the durations of the
# last runs as its "hours" attribute.
CONF_ENERGY_PER_RUN: Final = "energy_per_run"
DEFAULT_RUN_KWH: Final = 1.0
DEFAULT_RUN_HOURS: Final = 2.0
