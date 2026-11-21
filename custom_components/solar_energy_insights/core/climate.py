"""A whole year from part of one: the days the recorder lacks, filled from the
days it has.

A replay of half a year, scaled up to a whole one, takes the summer for the
winter: too much sun, and for a heat pump far too little heat. So each missing
day borrows a recorded day with a similar sun instead, as a weather generator
would (see `fill_year`), and the year is replayed whole.

The borrowed day keeps its own weather, but moved to the date it fills:

- its solar is scaled by how much sunlight reaches the top of the atmosphere on
  each date (`daily_extraterrestrial`), so a November day standing in for
  December gives a little less;
- its temperatures are the thermometer's own for those hours where it has
  them, as it usually does: it records all year, whatever else is missing.
  Where it has none, the borrowed day's departure from its season is laid on
  the typical temperature of the date it fills (`AnnualCycle`), so a mild day
  stays mild, but for January;
- the house's own use is taken as it was.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

import numpy as np
import numpy.typing as npt

from .scenario import Hour

YEAR_DAYS = 365
TROPICAL_YEAR = 365.25
# Fewer recorded whole days than this lend too few kinds of weather to fill a year.
MIN_DONOR_DAYS = 30
# The season's temperature needs this many days to fit.
MIN_TEMPERATURE_DAYS = 60
# A day's mean counts with at least this many hours of temperature.
MIN_TEMPERATURE_HOURS = 20
# With less than about ten months of days, the annual cycle's phase is held at
# the climate's, and only its mean and amplitude are fitted. The days must then
# span at least this much of the cycle's swing (from -1 at the coldest to 1 at
# the warmest), or the amplitude can't be told apart from the mean.
FREE_PHASE_DAYS = 300
MIN_SEASON_SPREAD = 0.5
# Mid-latitude land is coldest about four weeks after the winter solstice: a
# sinusoid fitted to Germany's 1991-2020 monthly means is lowest on day 17.
COLDEST_DAY_NORTH = 17.0
COLDEST_DAY_SOUTH = COLDEST_DAY_NORTH + TROPICAL_YEAR / 2


def declination_deg(day_of_year: float) -> float:
    """The sun's declination on a day of the year, in degrees.

    Cooper's approximation (P. I. Cooper, "The absorption of radiation in solar
    stills", Solar Energy 12, 1969), as given in Duffie & Beckman, *Solar
    Engineering of Thermal Processes*, eq. 1.6.1a:
    δ = 23.45° · sin(360° · (284 + n) / 365). Within about a degree of the exact
    value all year, which is plenty to match days with a similar sun.
    """
    return 23.45 * math.sin(math.radians(360 * (284 + day_of_year) / 365))


def daily_extraterrestrial(day_of_year: float, latitude: float) -> float:
    """The day's sunlight on a horizontal surface above the atmosphere,
    relative to the solar constant times a day.

    Duffie & Beckman, eq. 1.10.3, without its constant factor 24·3600·G_sc/π:
    H₀ ∝ (1 + 0.033 cos(360° n/365)) · (cos φ cos δ sin ω_s + ω_s sin φ sin δ),
    with the sunset hour angle ω_s from cos ω_s = -tan φ tan δ (eq. 1.6.10),
    held to [-1, 1] for polar day and night. Only ratios of it are used: how
    much more or less sun one date gets than another at the same place.
    """
    phi = math.radians(latitude)
    delta = math.radians(declination_deg(day_of_year))
    cos_ws = min(max(-math.tan(phi) * math.tan(delta), -1.0), 1.0)
    ws = math.acos(cos_ws)
    distance = 1 + 0.033 * math.cos(math.radians(360 * day_of_year / 365))
    return max(
        distance
        * (
            math.cos(phi) * math.cos(delta) * math.sin(ws)
            + ws * math.sin(phi) * math.sin(delta)
        ),
        0.0,
    )


@dataclass(frozen=True, slots=True)
class AnnualCycle:
    """The typical daily mean temperature through the year: a sinusoid, the
    first harmonic of the annual cycle, which carries nearly all of a
    mid-latitude climate's seasonal swing."""

    mean_c: float
    amplitude_c: float
    coldest_day: float

    def at(self, day_of_year: float) -> float:
        return self.mean_c - self.amplitude_c * math.cos(
            2 * math.pi * (day_of_year - self.coldest_day) / TROPICAL_YEAR
        )


def fit_annual_cycle(
    days_of_year: npt.ArrayLike, means_c: npt.ArrayLike, latitude: float
) -> AnnualCycle | None:
    """The annual cycle that fits the daily means best, by least squares
    (harmonic regression). With most of a year, the phase is fitted too;
    with less, it is held at the climate's coldest day for the hemisphere and
    only the mean and amplitude are fitted. None when the days can't tell."""
    n = np.asarray(days_of_year, dtype=float)
    t = np.asarray(means_c, dtype=float)
    if len(n) < MIN_TEMPERATURE_DAYS:
        return None
    w = 2 * np.pi * n / TROPICAL_YEAR
    if len(np.unique(np.round(n))) >= FREE_PHASE_DAYS:
        design = np.column_stack([np.ones_like(w), np.cos(w), np.sin(w)])
        a, b, c = np.linalg.lstsq(design, t, rcond=None)[0]
        amplitude = math.hypot(b, c)
        # a + b cos w + c sin w = a + A cos(w - θ), warmest at θ.
        warmest = math.atan2(c, b) * TROPICAL_YEAR / (2 * math.pi)
        coldest = (warmest + TROPICAL_YEAR / 2) % TROPICAL_YEAR
        return AnnualCycle(float(a), float(amplitude), coldest)
    coldest = COLDEST_DAY_NORTH if latitude >= 0 else COLDEST_DAY_SOUTH
    season = -np.cos(2 * np.pi * (n - coldest) / TROPICAL_YEAR)
    if np.ptp(season) < MIN_SEASON_SPREAD:
        return None
    design = np.column_stack([np.ones_like(season), season])
    a, amplitude = np.linalg.lstsq(design, t, rcond=None)[0]
    if amplitude <= 0:
        return None
    return AnnualCycle(float(a), float(amplitude), coldest % TROPICAL_YEAR)


@dataclass(frozen=True, slots=True)
class Year:
    """The year to replay: recorded hours, and borrowed ones where none were."""

    hours: list[Hour]
    # Whether the missing days could be filled; if not, `hours` is what was
    # recorded, and a replay of it is scaled to a year.
    filled: bool
    recorded_hours: int
    filled_hours: int
    # Each calendar month's share of borrowed hours, January first.
    filled_share_by_month: tuple[float, ...] = (0.0,) * 12
    cycle: AnnualCycle | None = None
    # Borrowed hours that kept the thermometer's own reading.
    measured_temperature_hours: int = 0


def _day_of_year(d: date) -> int:
    return d.timetuple().tm_yday


def _sun_distance(a: int, b: int) -> tuple[float, int]:
    """How alike two dates' sun is: the gap between their declinations, then
    the days between them round the year, for days with the same sun."""
    calendar = abs(a - b)
    return (
        abs(declination_deg(a) - declination_deg(b)),
        min(calendar, YEAR_DAYS - calendar),
    )


def fill_year(
    hours: Sequence[Hour],
    end: datetime,
    latitude: float,
    seed: int = 0,
    temperatures: Mapping[datetime, float] | None = None,
) -> Year:
    """The year up to *end*, every missing hour borrowed from a recorded day.

    Each day with missing hours gets one donor, a whole recorded day drawn by
    the k-nearest-neighbour resampling of Rajagopalan & Lall ("A k-nearest-
    neighbor simulator for daily precipitation and other weather variables",
    Water Resources Research 35(10), 1999): of the k recorded days most like
    it (k = √n for n recorded days, as they suggest), the j-th nearest is
    drawn with a weight of 1/j. Drawing among several keeps the variety of the
    weather: always taking the single nearest day would repeat one November
    day all through December. The draw is seeded, so the same hours always
    make the same year.

    "Most like it" is first the sun, then, where the thermometer measured the
    day, its departure from the season: of the 3k days with the sun most like
    its own, the k whose departure is nearest, so a cold clear January day
    borrows a cold clear November one, with its sun, not a mild grey one.

    *temperatures* are the thermometer's hourly means over the whole year,
    also for hours without solar or house readings. They fit the annual cycle
    and stand in borrowed hours as measured.
    """
    end = end.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=YEAR_DAYS)
    recorded = {h.start: h for h in hours if start <= h.start < end}
    unfilled = Year(list(recorded.values()), False, len(recorded), 0)
    by_day: dict[date, list[Hour]] = defaultdict(list)
    for h in recorded.values():
        by_day[h.start.date()].append(h)
    donors = sorted(d for d, day in by_day.items() if len(day) == 24)
    if len(donors) < MIN_DONOR_DAYS:
        return unfilled

    cycle = None
    measured = {t: c for t, c in (temperatures or {}).items() if start <= t < end} or {
        h.start: h.outdoor_c for h in recorded.values() if h.outdoor_c is not None
    }
    means: dict[date, float] = {}
    if measured:
        per_day: dict[date, list[float]] = defaultdict(list)
        for t, c in measured.items():
            per_day[t.date()].append(c)
        means = {
            d: float(np.mean(temps))
            for d, temps in per_day.items()
            if len(temps) >= MIN_TEMPERATURE_HOURS
        }
        cycle = fit_annual_cycle(
            [_day_of_year(d) for d in means], list(means.values()), latitude
        )
        # A borrowed day needs a temperature to move.
        donors = [d for d in donors if all(h.outdoor_c is not None for h in by_day[d])]
        if cycle is None or len(donors) < MIN_DONOR_DAYS:
            return unfilled

    donor_days = np.array([_day_of_year(d) for d in donors])

    def departure(d: date) -> float | None:
        mean = means.get(d)
        return (
            None if cycle is None or mean is None else mean - cycle.at(_day_of_year(d))
        )

    donor_departure = [departure(d) for d in donors]
    hour_of = {d: {h.start.hour: h for h in by_day[d]} for d in donors}
    k = max(1, round(math.sqrt(len(donors))))
    weights = 1 / np.arange(1, k + 1)
    weights /= weights.sum()
    rng = np.random.default_rng(seed)
    sun_at = {n: daily_extraterrestrial(n, latitude) for n in range(1, 367)}

    out: list[Hour] = []
    borrowed = [0] * 12
    in_month = [0] * 12
    kept_temperature = 0
    chosen: dict[date, date] = {}
    t = start
    while t < end:
        month = t.month - 1
        in_month[month] += 1
        own = recorded.get(t)
        if own is not None:
            out.append(own)
            t += timedelta(hours=1)
            continue
        day = t.date()
        if day not in chosen:
            n = _day_of_year(day)
            distance = [_sun_distance(n, int(m)) for m in donor_days]
            near = sorted(range(len(donors)), key=distance.__getitem__)
            own_departure = departure(day)
            if own_departure is not None:
                near = sorted(
                    near[: 3 * k],
                    key=lambda i: abs((donor_departure[i] or 0.0) - own_departure),
                )
            near = near[:k]
            chosen[day] = donors[near[int(rng.choice(len(near), p=weights))]]
        donor_day = chosen[day]
        donor = hour_of[donor_day][t.hour]
        hour = _borrow(donor, t, day, donor_day, cycle, sun_at)
        if t in measured:
            hour = replace(hour, outdoor_c=measured[t])
            kept_temperature += 1
        out.append(hour)
        borrowed[month] += 1
        t += timedelta(hours=1)
    return Year(
        out,
        True,
        len(recorded),
        sum(borrowed),
        tuple(b / m if m else 0.0 for b, m in zip(borrowed, in_month, strict=True)),
        cycle,
        kept_temperature,
    )


def _borrow(
    donor: Hour,
    start: datetime,
    day: date,
    donor_day: date,
    cycle: AnnualCycle | None,
    sun_at: dict[int, float],
) -> Hour:
    """The donor's hour, moved to *start*: its sun scaled to the date's, its
    temperature's departure from its season laid on this date's season."""
    n, m = _day_of_year(day), _day_of_year(donor_day)
    ratio = sun_at[n] / sun_at[m] if sun_at[m] > 0 else 0.0
    outdoor = donor.outdoor_c
    if cycle is not None and outdoor is not None:
        outdoor = cycle.at(n) + (outdoor - cycle.at(m))
    return replace(
        donor,
        start=start,
        solar_kwh=donor.solar_kwh * ratio,
        outdoor_c=outdoor,
        strings=tuple((s, kwh * ratio) for s, kwh in donor.strings),
        metered_import_kwh=None,
        metered_export_kwh=None,
        measured=False,
    )
