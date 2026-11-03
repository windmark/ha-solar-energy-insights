"""The solar array: its planes, their size and where they face.

Read from Solcast's rooftop sites rather than guessed; the direction model says
what share of a south-facing plane's yearly energy each plane makes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from functools import lru_cache

import numpy as np

# W/m² outside the atmosphere; thinned by air mass (Meinel's approximation).
_SOLAR = 1353.0
# About half in a cloudy climate like central Europe's: what keeps planes
# facing away from the sun useful.
_DIFFUSE_SHARE = 0.45
_ALBEDO = 0.2


@dataclass(frozen=True, slots=True)
class Plane:
    """One direction of panels."""

    kwp: float
    # Clockwise from north: 90 east, 180 south.
    compass_deg: float
    tilt_deg: float
    name: str = ""


@dataclass(frozen=True, slots=True)
class Array:
    planes: tuple[Plane, ...]
    latitude: float
    # Overrides the planes' total when set.
    kwp_override: float | None = None

    @property
    def kwp(self) -> float | None:
        if self.kwp_override:
            return self.kwp_override
        total = sum(p.kwp for p in self.planes)
        return total or None

    @property
    def relative_yield(self) -> float:
        """A year's yield per kWp, as a share of the best plane's (1 = ideal)."""
        if not self.planes:
            return 1.0
        total = sum(p.kwp for p in self.planes)
        if total <= 0:
            return 1.0
        return (
            sum(
                p.kwp * plane_yield(p.compass_deg, p.tilt_deg, self.latitude)
                for p in self.planes
            )
            / total
        )


def _sun_paths(latitude: float) -> tuple[np.ndarray, np.ndarray]:
    """Sun elevation and compass azimuth, radians, every 15 minutes of every
    fifth day of a year, by solar time."""
    lat = math.radians(latitude)
    days = np.arange(1, 366, 5)
    hours = np.arange(0, 24, 0.25)
    d, h = np.meshgrid(days, hours, indexing="ij")
    decl = np.radians(23.44) * np.sin(2 * np.pi * (284 + d) / 365)
    hour_angle = np.radians(15 * (h - 12))
    sin_elev = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(
        hour_angle
    )
    elev = np.arcsin(np.clip(sin_elev, -1, 1))
    azimuth = (
        np.arctan2(
            np.sin(hour_angle),
            np.cos(hour_angle) * np.sin(lat) - np.tan(decl) * np.cos(lat),
        )
        + np.pi
    )
    up = elev > np.radians(2)
    return elev[up], azimuth[up]


def _energy(
    compass_deg: float, tilt_deg: float, elev: np.ndarray, az: np.ndarray
) -> float:
    tilt, facing = math.radians(tilt_deg), math.radians(compass_deg)
    air_mass = 1 / np.sin(elev)
    beam = _SOLAR * 0.7 ** (air_mass**0.678)
    incidence = np.sin(elev) * math.cos(tilt) + np.cos(elev) * math.sin(tilt) * np.cos(
        az - facing
    )
    direct_h = beam * np.sin(elev)
    diffuse_h = direct_h * _DIFFUSE_SHARE / (1 - _DIFFUSE_SHARE)
    sky = diffuse_h * (1 + math.cos(tilt)) / 2
    ground = _ALBEDO * (direct_h + diffuse_h) * (1 - math.cos(tilt)) / 2
    return float(np.sum(beam * np.clip(incidence, 0, None) + sky + ground))


@lru_cache(maxsize=256)
def plane_yield(compass_deg: float, tilt_deg: float, latitude: float) -> float:
    """A plane's year against the best plane at this latitude."""
    elev, az = _sun_paths(round(latitude, 1))
    toward_equator = 180.0 if latitude >= 0 else 0.0
    best = max(_energy(toward_equator, t, elev, az) for t in range(0, 75, 5))
    return min(_energy(compass_deg, tilt_deg, elev, az) / best, 1.0)


def from_sites(sites: Sequence[dict[str, object]], latitude: float) -> Array:
    """Planes from Solcast's rooftop sites: DC capacity (else AC), compass
    direction (else Solcast's azimuth, where north is 0 and east -90) and tilt."""
    planes = []
    for site in sites:
        kwp = _number(site.get("capacity_dc")) or _number(site.get("capacity"))
        if not kwp:
            continue
        compass = _number(site.get("compass_degrees"))
        if compass is None:
            azimuth = _number(site.get("azimuth"))
            compass = 180.0 if azimuth is None else (-azimuth) % 360
        tilt = _number(site.get("tilt"))
        planes.append(
            Plane(
                kwp,
                compass,
                30.0 if tilt is None else tilt,
                str(site.get("name") or ""),
            )
        )
    return Array(tuple(planes), latitude)


def _number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None


# A clear sky has little diffuse light; 85 % is a standard system efficiency
# (inverter, wiring, heat).
_CLEAR_DIFFUSE_SHARE = 0.12
_PERFORMANCE = 0.85


def clear_day_kwh(
    array: Array,
    day: date,
    tz: tzinfo,
    longitude: float,
    after: datetime | None = None,
) -> float:
    """What the array would make on *day* under a clear sky, kWh; from *after*
    on, if given. Each plane by its own direction and tilt; solar time from the
    longitude."""
    if not array.planes:
        return 0.0
    lat = math.radians(array.latitude)
    doy = day.timetuple().tm_yday
    decl = math.radians(23.44) * math.sin(2 * math.pi * (284 + doy) / 365)
    start = datetime.combine(day, datetime.min.time(), tz)
    steps = [start + timedelta(minutes=15 * k) for k in range(96)]
    if after is not None:
        steps = [t for t in steps if t >= after]
    total = 0.0
    for t in steps:
        utc = t.astimezone(UTC)
        solar_hour = utc.hour + utc.minute / 60 + longitude / 15
        hour_angle = math.radians(15 * (solar_hour - 12))
        sin_elev = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(
            decl
        ) * math.cos(hour_angle)
        if sin_elev <= math.sin(math.radians(2)):
            continue
        elev = math.asin(sin_elev)
        az = (
            math.atan2(
                math.sin(hour_angle),
                math.cos(hour_angle) * math.sin(lat) - math.tan(decl) * math.cos(lat),
            )
            + math.pi
        )
        beam = _SOLAR * 0.7 ** ((1 / sin_elev) ** 0.678)
        direct_h = beam * sin_elev
        diffuse_h = direct_h * _CLEAR_DIFFUSE_SHARE / (1 - _CLEAR_DIFFUSE_SHARE)
        for plane in array.planes:
            tilt, facing = math.radians(plane.tilt_deg), math.radians(plane.compass_deg)
            incidence = sin_elev * math.cos(tilt) + math.cos(elev) * math.sin(
                tilt
            ) * math.cos(az - facing)
            poa = beam * max(incidence, 0.0) + diffuse_h * (1 + math.cos(tilt)) / 2
            poa += _ALBEDO * (direct_h + diffuse_h) * (1 - math.cos(tilt)) / 2
            # 1000 W/m² on the panels is their rated kWp.
            total += poa / 1000 * plane.kwp * _PERFORMANCE * 0.25
    return round(total, 2)
