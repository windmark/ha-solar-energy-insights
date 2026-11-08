"""Plan the battery until the next sunrise.

Facts only: the wording lives in summary.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, tzinfo
from enum import StrEnum

import numpy as np

from . import batch
from .batch import Floats, Slices
from .battery import Battery, Rate, Simulation, simulate
from .forecast import PvForecast, Quantile
from .nights import HourlyUse, PastDay, past_days, recent_use, typical

FREE_POWER_FULL_WITHIN_PCT = 3.0
FREE_POWER_MIN_EXPORT_KW = 1.5
WINDOW_MIN_SPARE_KW = 1.0
HEAVY_SHARE = 0.8
HALF_HOUR = timedelta(minutes=30)
CHARGE_STEP_PCT = 0.5


@dataclass(frozen=True, slots=True)
class PlanInputs:
    now: datetime
    tz: tzinfo
    sunrise: datetime
    sunset: datetime
    battery: Battery
    pv: PvForecast
    use: HourlyUse
    nights: int = 14
    export_kw: float = 0.0
    extra: Rate | None = None
    # Load the house meter never sees (the inverter's own standby), learned
    # from how real nights ended.
    standby_kw: float = 0.0
    # Calibrated on real nights so a "heavy" night is exceeded as often as intended.
    typical_share: float = 0.5
    heavy_share: float = HEAVY_SHARE
    # Where charging stops right now, if lowered below the battery's ceiling
    # for a while, such as to keep room for midday. None: at the ceiling.
    charge_limit_now_pct: float | None = None

    @property
    def sun_up(self) -> bool:
        return self.sunset < self.sunrise

    @property
    def limit_now_pct(self) -> float:
        return (
            self.battery.ceiling_pct
            if self.charge_limit_now_pct is None
            else self.charge_limit_now_pct
        )


class Outlook(StrEnum):
    LASTS = "lasts"
    BORDERLINE = "borderline"
    GRID = "grid"


@dataclass(frozen=True, slots=True)
class NightOutcome:
    """One past night's use, replayed from now to sunrise."""

    days_ago: int
    lasts: bool
    reserve_at: datetime | None
    load_kwh: float
    grid_kwh: float
    spare_kwh: float
    # Split at local midnight.
    evening_kwh: float = 0.0
    late_kwh: float = 0.0


@dataclass(frozen=True, slots=True)
class Night:
    outcomes: tuple[NightOutcome, ...]
    typical: NightOutcome
    heavy: NightOutcome
    # Battery % of each replayed night at `times`.
    times: tuple[datetime, ...] = field(default=(), repr=False, compare=False)
    levels: tuple[tuple[float, ...], ...] = field(default=(), repr=False, compare=False)
    # The evening's share leaves the usual after-midnight use for the late night.
    budget_kwh: float | None = None
    evening_budget_kwh: float | None = None

    @property
    def lasting(self) -> int:
        return sum(outcome.lasts for outcome in self.outcomes)

    @property
    def outlook(self) -> Outlook:
        """Lasts on a heavy night too, only on a typical one, or not even that."""
        if not self.typical.lasts:
            return Outlook.GRID
        return Outlook.LASTS if self.heavy.lasts else Outlook.BORDERLINE

    @property
    def share(self) -> float:
        return sum(o.lasts for o in self.outcomes) / len(self.outcomes)


@dataclass(frozen=True, slots=True)
class Charge:
    """The battery until the next sunset, for the expected and the cloudy case."""

    full_at: datetime | None
    full_at_cloudy: datetime | None
    peak_pct: float
    peak_pct_cloudy: float
    sunset_pct: float | None


@dataclass(frozen=True, slots=True)
class Surplus:
    """Solar the battery cannot take before sunset: what other loads could use."""

    after_full_kwh: float
    while_charging_kwh: float
    cloudy_kwh: float

    @property
    def total_kwh(self) -> float:
        return self.after_full_kwh + self.while_charging_kwh


@dataclass(frozen=True, slots=True)
class Needed:
    """The lowest charge the night lasts from: at sunset by day, now at night.

    The percentages are None when even a full battery falls short.
    """

    at: datetime
    typical_pct: float | None
    heavy_pct: float | None
    # Grid energy still needed when starting full isn't enough.
    typical_short_kwh: float = 0.0
    heavy_short_kwh: float = 0.0


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime
    kwh: float


@dataclass(frozen=True, slots=True)
class Day:
    """The expected rest of the day until sunset, to show the working."""

    pv_kwh: float
    load_kwh: float
    # Can come before sunset: this morning, before the sun takes over.
    reserve_at: datetime | None
    curve_cloudy: tuple[tuple[datetime, float], ...] = field(repr=False)
    # (start, solar kW, house kW) per half hour.
    slots: tuple[tuple[datetime, float, float], ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class Plan:
    now: datetime
    sunrise: datetime
    sunset: datetime
    charge: Charge
    surplus: Surplus
    # None until the recorder holds a day of house use.
    night: Night | None
    needed: Needed | None
    best_window: Window | None
    free_power: bool
    free_until: datetime | None
    charge_limit_pct: float
    # Where charging stops right now: below the limit while it is lowered.
    charge_limit_now_pct: float
    curve: tuple[tuple[datetime, float], ...] = field(repr=False)
    day: Day | None = field(default=None, repr=False, compare=False)

    @property
    def sun_up(self) -> bool:
        return self.sunset < self.sunrise


def _plus(base: Rate, extra: Rate | None) -> Rate:
    if extra is None:
        return base
    return lambda t: base(t) + extra(t)


def _house(i: PlanInputs, base: Rate) -> Rate:
    """The house's use plus any extra load and the unmetered standby."""
    load = _plus(base, i.extra)
    if i.standby_kw <= 0:
        return load
    standby = i.standby_kw
    return lambda t: load(t) + standby


def _sun(pv: PvForecast, quantile: Quantile) -> Rate:
    return lambda t: pv.kw_at(t, quantile)


def _today(i: PlanInputs) -> date:
    return i.now.astimezone(i.tz).date()


def _days(i: PlanInputs) -> tuple[list[PastDay], Floats]:
    days = past_days(i.use, i.tz, i.nights, _today(i))
    # Equal weights: weighting nights by similarity didn't predict better.
    return days, np.ones(len(days))


def _loads(i: PlanInputs, slices: Slices, days: list[PastDay]) -> Floats:
    """Each past day's load over the slices ahead: one row per day."""
    return np.array([slices.sample(_house(i, day.rate)) for day in days])


def _night(
    i: PlanInputs, days: list[PastDay], weights: Floats, sun: Rate
) -> Night | None:
    """Replay the last nights from now until sunrise, all at once.

    By day only what happens after sunset counts: reaching the reserve this
    morning, before the sun takes over, isn't a night that failed.
    """
    if not days:
        return None
    mark = i.sunset if i.sun_up else None
    slices = Slices.between(i.now, i.sunrise, mark)
    runs = batch.run(
        i.battery,
        np.full(len(days), i.battery.soc_pct),
        slices,
        slices.sample(sun),
        _loads(i, slices, days),
        record=True,
    )
    outcomes = tuple(
        NightOutcome(
            days_ago=day.days_ago,
            lasts=bool(runs.lasts[k]),
            reserve_at=runs.reserve_at[k],
            load_kwh=float(runs.load_kwh[k]),
            grid_kwh=float(runs.grid_kwh[k]),
            spare_kwh=float(runs.spare_kwh[k]),
            evening_kwh=_used(i, day, night_start(i), _midnight(i)),
            late_kwh=_used(i, day, max(_midnight(i), night_start(i)), i.sunrise),
        )
        for k, day in enumerate(days)
    )
    # Rank by how a night ends, not by its load, so a heavy night never looks
    # better than a typical one.
    badness = np.where(runs.grid_kwh > 0, runs.grid_kwh, -runs.spare_kwh)

    def at(share: float) -> NightOutcome:
        target = batch.weighted_quantile(badness, weights, share)
        k = int(np.argmin(np.abs(badness - target) - weights * 1e-9))
        return outcomes[k]

    assert runs.levels_pct is not None
    return Night(
        outcomes=outcomes,
        typical=at(i.typical_share),
        heavy=at(i.heavy_share),
        times=(*slices.starts, i.sunrise),
        levels=tuple(tuple(round(float(v), 1) for v in row) for row in runs.levels_pct),
    )


def _needed(
    i: PlanInputs, days: list[PastDay], weights: Floats, sun: Rate
) -> Needed | None:
    """The charge needed for the night: at sunset by day, now at night.

    Every level from the floor to the charge limit is replayed against every
    past night at once; typical and heavy are quantiles of the lowest level
    each night lasts from.
    """
    if not days:
        return None
    start = i.sunset if i.sun_up else i.now
    slices = Slices.between(start, i.sunrise, None)
    # A level the battery never charges to is no answer.
    ceiling = i.battery.ceiling_pct
    levels = np.unique(
        np.append(np.arange(i.battery.floor_pct, ceiling, CHARGE_STEP_PCT), ceiling)
    )
    loads = _loads(i, slices, days)
    # Level-major: run (l, d) is level l against night d.
    runs = batch.run(
        i.battery,
        np.repeat(levels, len(days)),
        slices,
        slices.sample(sun),
        np.tile(loads, (len(levels), 1)),
    )
    lasts = runs.lasts.reshape(len(levels), len(days))
    lowest = np.where(lasts.any(axis=0), levels[np.argmax(lasts, axis=0)], np.inf)
    short_from_full = runs.grid_kwh.reshape(len(levels), len(days))[-1]

    def pct(share: float) -> float | None:
        value = batch.weighted_quantile(lowest, weights, share)
        return None if math.isinf(value) else round(value, 1)

    def short(share: float) -> float:
        return batch.weighted_quantile(short_from_full, weights, share)

    typical, heavy = pct(i.typical_share), pct(i.heavy_share)
    return Needed(
        at=start,
        typical_pct=typical,
        heavy_pct=heavy,
        typical_short_kwh=short(i.typical_share) if typical is None else 0.0,
        heavy_short_kwh=short(i.heavy_share) if heavy is None else 0.0,
    )


def _day(i: PlanInputs, load: Rate, expected: Simulation, cloudy: Simulation) -> Day:
    start = _slot(i.now if i.sun_up else i.sunrise)
    slots = []
    t = start
    while t < i.sunset:
        slots.append((t, round(i.pv.kw_at(t), 3), round(load(t), 3)))
        t += HALF_HOUR
    return Day(
        pv_kwh=expected.pv_kwh,
        load_kwh=expected.load_kwh,
        reserve_at=expected.reserve_at,
        curve_cloudy=cloudy.points,
        slots=tuple(slots),
    )


def _charge(
    i: PlanInputs, load: Rate
) -> tuple[Charge, Surplus, Simulation, Simulation]:
    expected = simulate(
        i.battery, i.now, i.sunset, _sun(i.pv, Quantile.P50), load, mark_at=i.sunset
    )
    cloudy = simulate(i.battery, i.now, i.sunset, _sun(i.pv, Quantile.P10), load)
    charge = Charge(
        full_at=expected.full_at,
        full_at_cloudy=cloudy.full_at,
        peak_pct=expected.peak_pct,
        peak_pct_cloudy=cloudy.peak_pct,
        sunset_pct=expected.mark_pct,
    )
    surplus = Surplus(
        after_full_kwh=expected.export_kwh,
        while_charging_kwh=expected.export_before_full_kwh,
        cloudy_kwh=cloudy.export_kwh + cloudy.export_before_full_kwh,
    )
    return charge, surplus, expected, cloudy


def _slot(t: datetime) -> datetime:
    return t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)


def _best_window(i: PlanInputs, load: Rate) -> Window | None:
    """The run of half hours before sunset with the most solar to spare."""
    t = _slot(i.now if i.sun_up else i.sunrise)
    best: Window | None = None
    run_start: datetime | None = None
    run_kwh = 0.0
    while t < i.sunset:
        spare = i.pv.kw_at(t) - load(t)
        if spare >= WINDOW_MIN_SPARE_KW:
            if run_start is None:
                run_start, run_kwh = t, 0.0
            run_kwh += spare * 0.5
            if best is None or run_kwh > best.kwh:
                best = Window(run_start, t + HALF_HOUR, run_kwh)
        else:
            run_start = None
        t += HALF_HOUR
    return best


def _free_until(i: PlanInputs, load: Rate) -> datetime | None:
    """When the expected solar stops beating the house by the free-power margin."""
    t = _slot(i.now) + HALF_HOUR
    while t < i.sunset:
        if i.pv.kw_at(t) - load(t) < FREE_POWER_MIN_EXPORT_KW:
            return t
        t += HALF_HOUR
    return i.sunset if i.sun_up else None


NOON = 12


def night_start(i: PlanInputs) -> datetime:
    """When the night begins: tonight's sunset, or now if it has set."""
    return i.sunset if i.sun_up else i.now


def _midnight(i: PlanInputs) -> datetime:
    """The midnight that splits tonight; after midnight, the night's start."""
    start = night_start(i).astimezone(i.tz)
    if start.hour < NOON:
        return start
    return datetime.combine(start.date() + timedelta(days=1), time(), i.tz)


def _used(i: PlanInputs, day: PastDay, start: datetime, end: datetime) -> float:
    """A past day's house use (with the hidden load) between two of tonight's
    moments, in kWh."""
    if end <= start:
        return 0.0
    rate = _house(i, day.rate)
    step = timedelta(minutes=15)
    total, t = 0.0, start
    while t < end:
        until = min(t + step, end)
        total += rate(t) * (until - t).total_seconds() / 3600
        t = until
    return round(total, 3)


def _budget(i: PlanInputs, night: Night, start_pct: float) -> Night:
    """What the battery can deliver from the night's start, and how much of it
    the evening may use."""
    b = i.battery
    budget = max(start_pct - b.floor_pct, 0.0) / 100 * b.capacity_kwh * b.eta
    late = float(np.median([o.late_kwh for o in night.outcomes]))
    return replace(
        night,
        budget_kwh=round(budget, 2),
        evening_budget_kwh=round(max(budget - late, 0.0), 2),
    )


def replay_night(i: PlanInputs) -> Night | None:
    """Only the night: each of the last nights replayed from now to sunrise."""
    days, weights = _days(i)
    return _night(i, days, weights, _sun(i.pv, Quantile.P50))


def plan(i: PlanInputs) -> Plan:
    """Answer the questions for the moment in *i*."""
    days, weights = _days(i)
    # The same recent days as the night, so the day and the night agree.
    recent = recent_use(i.use, _today(i), [d.days_ago for d in days])
    load = _house(i, typical(recent, i.tz))
    charge, surplus, expected, cloudy = _charge(i, load)
    sun = _sun(i.pv, Quantile.P50)
    night = _night(i, days, weights, sun)
    if night is not None:
        start_pct = charge.sunset_pct if i.sun_up else i.battery.soc_pct
        night = _budget(
            i, night, start_pct if start_pct is not None else i.battery.soc_pct
        )
    # Free while the battery takes no more now, even if only for a while.
    free = (
        i.battery.soc_pct >= i.limit_now_pct - FREE_POWER_FULL_WITHIN_PCT
        and i.export_kw >= FREE_POWER_MIN_EXPORT_KW
    )
    return Plan(
        now=i.now,
        sunrise=i.sunrise,
        sunset=i.sunset,
        charge=charge,
        surplus=surplus,
        night=night,
        needed=_needed(i, days, weights, sun),
        best_window=_best_window(i, load),
        free_power=free,
        free_until=_free_until(i, load) if free else None,
        charge_limit_pct=i.battery.ceiling_pct,
        charge_limit_now_pct=i.limit_now_pct,
        curve=expected.points,
        day=_day(i, load, expected, cloudy),
    )
