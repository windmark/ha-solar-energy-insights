"""The solar array as configured: its planes, their size and where they face.

Size is read, not guessed: Solcast's rooftop sites carry each plane's DC
capacity, compass direction and tilt. Where a plane faces changes how much a
kWp makes in a year: an east–west roof makes less per kWp than one facing the
equator, so anything measured per kWp is scaled by the array's relative yield.

Relative yield comes from a sun model over a whole year: the energy a plane
would catch, direct and diffuse, against the best plane at the same latitude
(facing the equator at its best tilt). It ignores shading; what it captures is
geometry. It agrees with PVGIS for central Europe to a few percent: east or west
at 30° about 0.83, north at 30° about 0.6, flat about 0.88.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

# Clear-sky beam irradiance, W/m²: 1353 W/m² outside the air, thinned with the
# air mass the light passes through (Meinel's approximation).
_SOLAR = 1353.0
# Diffuse light, as a share of the year's light on a horizontal surface. Around
# half in a cloudy climate like central Europe's; light from the whole sky is
# what keeps planes facing away from the sun useful.
_DIFFUSE_SHARE = 0.45
# Light reflected from the ground, seen by tilted planes.
_ALBEDO = 0.2


@dataclass(frozen=True, slots=True)
class Plane:
    """One direction of panels."""

    kwp: float
    # Where it faces, degrees clockwise from north: 90 east, 180 south.
    compass_deg: float
    # Degrees from horizontal.
    tilt_deg: float
    name: str = ""


@dataclass(frozen=True, slots=True)
class Array:
    planes: tuple[Plane, ...]
    latitude: float
    # Set by hand when no planes are known; otherwise the planes' total.
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
    except (TypeError, ValueError):
        return None
