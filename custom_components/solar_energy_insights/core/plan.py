"""The answers: will the battery fill, will it last the night, when to run heavy
loads, how much to charge for the night, and how much solar is left over.
Facts only; wording is left to whoever shows them."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo

from .battery import Battery, Rate, Simulation, simulate
from .forecast import PvForecast, Quantile
from .nights import (
    HourlyUse,
    PastDay,
    days_covered,
    past_days,
    typical,
    weighted_quantile,
)

# Battery at least this full and exporting at least this much = free power.
FREE_SOC_PCT = 97.0
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
    weights: Sequence[float] | None = None


@dataclass(frozen=True, slots=True)
class NightOutcome:
    """One past night's use, replayed from now to sunrise."""

    days_ago: int
    weight: float
    lasts: bool
    reserve_at: datetime | None
    load_kwh: float
    grid_kwh: float
    spare_kwh: float


@dataclass(frozen=True, slots=True)
class Night:
    outcomes: tuple[NightOutcome, ...]
    typical: NightOutcome
    heavy: NightOutcome

    @property
    def lasting(self) -> int:
        return sum(outcome.lasts for outcome in self.outcomes)

    @property
    def share(self) -> float:
        """The weighted share of nights the battery lasts."""
        total = sum(o.weight for o in self.outcomes)
        return sum(o.weight for o in self.outcomes if o.lasts) / total


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
    """The lowest state of charge now from which the night lasts."""

    typical_pct: float | None
    heavy_pct: float | None


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime
    kwh: float


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
    curve: tuple[tuple[datetime, float], ...] = field(repr=False)


def _plus(base: Rate, extra: Rate | None) -> Rate:
    if extra is None:
        return base
    return lambda t: base(t) + extra(t)


def _sun(pv: PvForecast, quantile: Quantile) -> Rate:
    return lambda t: pv.kw_at(t, quantile)


def _replay(i: PlanInputs, battery: Battery, day: PastDay, sun: Rate) -> Simulation:
    mark = i.sunset if i.sunset < i.sunrise else None
    return simulate(battery, i.now, i.sunrise, sun, _plus(day.rate, i.extra), mark)


def _days(i: PlanInputs) -> tuple[list[PastDay], list[float]]:
    available = min(i.nights, days_covered(i.use))
    days = past_days(i.use, i.tz, available)
    weights = list(i.weights[: len(days)]) if i.weights else []
    if len(weights) != len(days) or sum(weights) <= 0:
        weights = [1.0] * len(days)
    return days, weights


def _night(
    i: PlanInputs, days: list[PastDay], weights: list[float], sun: Rate
) -> Night | None:
    """Replay each of the last nights from now until sunrise."""
    if not days:
        return None
    outcomes = []
    for day, weight in zip(days, weights, strict=True):
        sim = _replay(i, i.battery, day, sun)
        outcomes.append(
            NightOutcome(
                days_ago=day.days_ago,
                weight=weight,
                lasts=sim.lasts,
                reserve_at=sim.reserve_at,
                load_kwh=sim.load_kwh,
                grid_kwh=sim.grid_kwh,
                spare_kwh=sim.spare_kwh,
            )
        )
    by_load = [(o.load_kwh, o.weight) for o in outcomes]

    def at(share: float) -> NightOutcome:
        load = weighted_quantile(by_load, share)
        return min(outcomes, key=lambda o: (abs(o.load_kwh - load), -o.weight))

    return Night(outcomes=tuple(outcomes), typical=at(0.5), heavy=at(HEAVY_SHARE))


def _needed_for(i: PlanInputs, day: PastDay, sun: Rate) -> float | None:
    """The lowest state of charge now from which this night lasts, or None if
    even a full battery does not."""

    def lasts(soc: float) -> bool:
        return _replay(i, replace(i.battery, soc_pct=soc), day, sun).lasts

    floor = i.battery.floor_pct
    if lasts(floor):
        return floor
    if not lasts(100.0):
        return None
    low, high = floor, 100.0
    while high - low > CHARGE_STEP_PCT:
        middle = (low + high) / 2
        if lasts(middle):
            high = middle
        else:
            low = middle
    return round(high, 1)


def _needed(
    i: PlanInputs, night: Night | None, days: list[PastDay], sun: Rate
) -> Needed | None:
    if night is None:
        return None
    by_age = {day.days_ago: day for day in days}
    return Needed(
        typical_pct=_needed_for(i, by_age[night.typical.days_ago], sun),
        heavy_pct=_needed_for(i, by_age[night.heavy.days_ago], sun),
    )


def _charge(i: PlanInputs, load: Rate) -> tuple[Charge, Surplus, Simulation]:
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
    return charge, surplus, expected


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


def replay_night(i: PlanInputs) -> Night | None:
    """Only the night: each of the last nights replayed from now to sunrise."""
    days, weights = _days(i)
    return _night(i, days, weights, _sun(i.pv, Quantile.P50))


def plan(i: PlanInputs) -> Plan:
    """Answer the questions for the moment in *i*."""
    load = _plus(typical(i.use, i.tz), i.extra)
    charge, surplus, expected = _charge(i, load)
    sun = _sun(i.pv, Quantile.P50)
    days, weights = _days(i)
    night = _night(i, days, weights, sun)
    free = i.battery.soc_pct >= FREE_SOC_PCT and i.export_kw >= FREE_EXPORT_KW
    return Plan(
        now=i.now,
        sunrise=i.sunrise,
        sunset=i.sunset,
        charge=charge,
        surplus=surplus,
        night=night,
        needed=_needed(i, night, days, sun),
        best_window=_best_window(i, load),
        free_power=free,
        free_until=_free_until(i, load) if free else None,
        curve=expected.points,
    )
