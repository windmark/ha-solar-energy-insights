"""The answers: will the battery fill, will it last the night, when to run heavy
loads, how much to charge for the night, and how much solar is left over.
Facts only; wording is left to whoever shows them."""

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

# Battery at least this full and exporting at least this much = free power.
# Within this many points of the charge limit counts as full, for free power.
FREE_MARGIN_PCT = 3.0
FREE_EXPORT_KW = 1.5
# Spare solar from which a half hour counts towards a window for heavy loads.
WINDOW_SURPLUS_KW = 1.0
# The night that stands for "a heavy night": this share of the weight used less.
HEAVY_SHARE = 0.8
HALF_HOUR = timedelta(minutes=30)
# Charge needed is found to this many percentage points.
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
    # Load on top of the house's usual, such as an appliance started now.
    extra: Rate | None = None
    # How much each of the last nights counts, most recent first; None = equally.
    # Load the house counter never sees, such as the inverter's own standby,
    # learned from how real nights ended.
    standby_kw: float = 0.0
    # Where in the range of replayed nights "typical" and "heavy" sit: the
    # weighted median, and a share calibrated so heavy is exceeded about as
    # often as intended on real nights.
    typical_share: float = 0.5
    heavy_share: float = HEAVY_SHARE


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
    # The night's use split at local midnight: the evening, and the rest.
    evening_kwh: float = 0.0
    late_kwh: float = 0.0


@dataclass(frozen=True, slots=True)
class Night:
    outcomes: tuple[NightOutcome, ...]
    typical: NightOutcome
    heavy: NightOutcome
    # Each replayed night's battery level in %, at these moments: for drawing.
    times: tuple[datetime, ...] = field(default=(), repr=False, compare=False)
    levels: tuple[tuple[float, ...], ...] = field(default=(), repr=False, compare=False)
    # What the battery can deliver from the night's start (sunset, or now) down
    # to its reserve; and the part of it the evening may use, leaving the usual
    # after-midnight use for the rest of the night.
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
        """The share of nights the battery lasts."""
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
    """The lowest state of charge from which the night lasts, at *at*: sunset
    in the day, now at night. None when even a full battery does not."""

    at: datetime
    typical_pct: float | None
    heavy_pct: float | None
    # What would still come from the grid starting full, when even that is short.
    typical_short_kwh: float = 0.0
    heavy_short_kwh: float = 0.0


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime
    kwh: float


@dataclass(frozen=True, slots=True)
class Day:
    """The rest of the day until sunset, as expected, for showing the working."""

    # Solcast's expected solar, and the house's usual use, until sunset.
    pv_kwh: float
    load_kwh: float
    # When the battery would hand over to the grid before sunset, such as this
    # morning before the sun takes over; None if it does not.
    reserve_at: datetime | None
    # The battery's level if it is cloudier, as Solcast's low case.
    curve_cloudy: tuple[tuple[datetime, float], ...] = field(repr=False)
    # Each half hour until sunset: its start, expected solar kW and house kW.
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
    # The level charging stops at, as planned with.
    charge_limit_pct: float
    curve: tuple[tuple[datetime, float], ...] = field(repr=False)
    day: Day | None = field(default=None, repr=False, compare=False)


def _plus(base: Rate, extra: Rate | None) -> Rate:
    if extra is None:
        return base
    return lambda t: base(t) + extra(t)


def _house(i: PlanInputs, base: Rate) -> Rate:
    """The load to plan with: the house's use, plus any extra load asked about,
    plus what the house counter never sees."""
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
    # Every recent night counts the same.
    return days, np.ones(len(days))


def _loads(i: PlanInputs, slices: Slices, days: list[PastDay]) -> Floats:
    """Each past day's load over the slices ahead: one row per day."""
    return np.array([slices.sample(_house(i, day.rate)) for day in days])


def _night(
    i: PlanInputs, days: list[PastDay], weights: Floats, sun: Rate
) -> Night | None:
    """Replay each of the last nights from now until sunrise, all at once.

    In the day, the replay runs through the rest of today, but only what happens
    from sunset on is the night: reaching the reserve this morning, before the
    sun takes over, is not a night that failed.
    """
    if not days:
        return None
    mark = i.sunset if i.sunset < i.sunrise else None
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
    # How badly each night ends: grid energy needed, else minus the spare left.
    # Ranking by how a night ends, not by its load, keeps a heavy night from ever
    # looking better than a typical one.
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
    """The charge needed for the night, at sunset in the day and now at night.

    Every battery level from the floor to full, in steps, is replayed against
    every past night at once. Each night needs the lowest level it lasts from;
    typical and heavy are the weighted quantiles of those. Where even a full
    battery is short, the same runs say by how much.
    """
    if not days:
        return None
    start = i.sunset if i.sunset < i.sunrise else i.now
    slices = Slices.between(start, i.sunrise, None)
    # Up to the charge limit: a level the battery never charges to is no answer.
    ceiling = i.battery.ceiling_pct
    levels = np.unique(
        np.append(np.arange(i.battery.floor_pct, ceiling, CHARGE_STEP_PCT), ceiling)
    )
    loads = _loads(i, slices, days)
    # Runs ordered level-major: run (l, d) is level l against night d.
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
    start = _slot(i.now if i.sunset < i.sunrise else i.sunrise)
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
    t = _slot(i.now if i.sunset < i.sunrise else i.sunrise)
    best: Window | None = None
    run_start: datetime | None = None
    run_kwh = 0.0
    while t < i.sunset:
        spare = i.pv.kw_at(t) - load(t)
        if spare >= WINDOW_SURPLUS_KW:
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
        if i.pv.kw_at(t) - load(t) < FREE_EXPORT_KW:
            return t
        t += HALF_HOUR
    return i.sunset if i.sunset < i.sunrise else None


# Before this hour, a night that has started is past its midnight.
NOON = 12


def night_start(i: PlanInputs) -> datetime:
    """When the night begins: tonight's sunset, or now if it has set."""
    return i.sunset if i.sunset < i.sunrise else i.now


def _midnight(i: PlanInputs) -> datetime:
    """The midnight that splits tonight. After midnight the evening is over:
    the split is the night's start, and all that is left is the late part."""
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
    # The day's usual load, from the same recent days the night replays.
    recent = recent_use(i.use, _today(i), [d.days_ago for d in days])
    load = _house(i, typical(recent, i.tz))
    charge, surplus, expected, cloudy = _charge(i, load)
    sun = _sun(i.pv, Quantile.P50)
    night = _night(i, days, weights, sun)
    if night is not None:
        # The night starts from the level expected at sunset, or from now.
        start_pct = charge.sunset_pct if i.sunset < i.sunrise else i.battery.soc_pct
        night = _budget(
            i, night, start_pct if start_pct is not None else i.battery.soc_pct
        )
    free = (
        i.battery.soc_pct >= i.battery.ceiling_pct - FREE_MARGIN_PCT
        and i.export_kw >= FREE_EXPORT_KW
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
        curve=expected.points,
        day=_day(i, load, expected, cloudy),
    )
