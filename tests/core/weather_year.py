"""A year of weather near Augsburg, built the same way every time: a seasonal
cycle fitted to German climate normals, day-to-day spells that persist, cloudy
and clear days, and a roof whose sun follows the sun's real path."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np

from custom_components.solar_energy_insights.core.climate import declination_deg
from custom_components.solar_energy_insights.core.scenario import Hour

LATITUDE, LONGITUDE = 48.4, 10.9
END = datetime(2026, 11, 20, 22, tzinfo=UTC)
# Germany's 1991-2020 normals: a 9.3 °C mean, ±8.8 K, coldest on day 17.
MEAN_C, AMPLITUDE_C, COLDEST_DAY = 9.3, 8.8, 17
# Day-to-day departures: an AR(1) spell with a 3.5 K spread.
SPELL, SPREAD_K = 0.8, 3.5


def weather_year(seed: int = 1, end: datetime = END) -> list[Hour]:
    rng = np.random.default_rng(seed)
    start = end - timedelta(days=365)
    phi = math.radians(LATITUDE)
    out = []
    anomaly = 0.0
    for d in range(365):
        day = start + timedelta(days=d)
        n = day.timetuple().tm_yday
        anomaly = SPELL * anomaly + rng.normal(0, SPREAD_K * math.sqrt(1 - SPELL**2))
        phase = math.cos(2 * math.pi * (n - COLDEST_DAY) / 365.25)
        summer = (1 - phase) / 2
        mean = MEAN_C - AMPLITUDE_C * phase + anomaly
        clear = float(np.clip(rng.beta(2, 2) * (0.6 + 0.5 * summer), 0.05, 1.0))
        delta = math.radians(declination_deg(n))
        for hour in range(24):
            solar_time = hour + 0.5 + LONGITUDE / 15
            w = math.radians(15 * (solar_time - 12))
            sin_elevation = math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(
                delta
            ) * math.cos(w)
            sun = max(sin_elevation, 0.0) ** 1.15
            # 8.8 kWp south, tilted (more in winter), 6 kWp north.
            solar = (8.8 * (1.25 - 0.3 * summer) + 6.0 * 0.75) * 0.8 * sun * clear
            local = (hour + 1) % 24
            house = (0.9 if 17 <= local < 22 else 0.35) * (1.1 - 0.2 * summer)
            swing = 3.5 * (0.6 + 0.8 * clear)
            temp = mean + swing * math.sin(2 * math.pi * (local - 9) / 24)
            out.append(Hour(day + timedelta(hours=hour), solar, house, temp))
    return out
