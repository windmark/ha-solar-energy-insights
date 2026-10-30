"""The three answers: will the battery fill, will it last the night, when to run
heavy loads. Facts only; wording is left to whoever shows them."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo

from .battery import Battery, Rate, Simulation, simulate
from .forecast import PvForecast, Quantile
from .nights import HourlyUse, days_covered, past_days, quantile, typical

# Battery at least this full and exporting at least this much = free power.
FREE_SOC_PCT = 97.0
FREE_EXPORT_KW = 1.5
# Spare solar from which a half hour counts towards a window for heavy loads.
WINDOW_SURPLUS_KW = 1.0
# The night that stands for "a heavy night": this share of nights used less.
HEAVY_SHARE = 0.8
HALF_HOUR = timedelta(minutes=30)


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


@dataclass(frozen=True, slots=True)
class NightOutcome:
    """One past night's use, replayed from now to sunrise."""

    days_ago: int
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
        return self.lasting / len(self.outcomes)


@dataclass(frozen=True, slots=True)
class Charge:
    """The battery until the next sunset, for the expected and the cloudy case."""

    full_at: datetime | None
    full_at_cloudy: datetime | None
    peak_pct: float
    peak_pct_cloudy: float
    sunset_pct: float | None


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
    # None until the recorder holds a day of house use.
    night: Night | None
    best_window: Window | None
    free_power: bool
    curve: tuple[tuple[datetime, float], ...] = field(repr=False)


def _plus(base: Rate, extra: Rate | None) -> Rate:
    if extra is None:
        return base
    return lambda t: base(t) + extra(t)


def _sun(pv: PvForecast, quantile: Quantile) -> Rate:
    return lambda t: pv.kw_at(t, quantile)


def _outcome(days_ago: int, sim: Simulation) -> NightOutcome:
    return NightOutcome(
        days_ago=days_ago,
        lasts=sim.lasts,
        reserve_at=sim.reserve_at,
        load_kwh=sim.load_kwh,
        grid_kwh=sim.grid_kwh,
        spare_kwh=sim.spare_kwh,
    )


def _night(i: PlanInputs, sun: Rate) -> Night | None:
    """Replay each of the last nights from now until sunrise."""
    available = min(i.nights, days_covered(i.use))
    if available == 0:
        return None
    mark = i.sunset if i.sunset < i.sunrise else None
    outcomes = tuple(
        _outcome(
            day.days_ago,
            simulate(i.battery, i.now, i.sunrise, sun, _plus(day.rate, i.extra), mark),
        )
        for day in past_days(i.use, i.tz, available)
    )
    by_load = sorted(outcomes, key=lambda outcome: outcome.load_kwh)
    loads = [outcome.load_kwh for outcome in by_load]
    heavy_load = quantile(loads, HEAVY_SHARE)
    return Night(
        outcomes=outcomes,
        typical=by_load[len(by_load) // 2],
        heavy=next(o for o in by_load if o.load_kwh >= heavy_load),
    )


def _charge(i: PlanInputs, load: Rate) -> tuple[Charge, Simulation]:
    expected = simulate(
        i.battery, i.now, i.sunset, _sun(i.pv, Quantile.P50), load, mark_at=i.sunset
    )
    cloudy = simulate(i.battery, i.now, i.sunset, _sun(i.pv, Quantile.P10), load)
    return (
        Charge(
            full_at=expected.full_at,
            full_at_cloudy=cloudy.full_at,
            peak_pct=expected.peak_pct,
            peak_pct_cloudy=cloudy.peak_pct,
            sunset_pct=expected.mark_pct,
        ),
        expected,
    )


def _best_window(i: PlanInputs, load: Callable[[datetime], float]) -> Window | None:
    """The run of half hours before sunset with the most solar to spare."""
    start = i.now if i.sunset < i.sunrise else i.sunrise
    t = start.replace(minute=0 if start.minute < 30 else 30, second=0, microsecond=0)
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


def plan(i: PlanInputs) -> Plan:
    """Answer the three questions for the moment in *i*."""
    load = _plus(typical(i.use, i.tz), i.extra)
    charge, expected = _charge(i, load)
    return Plan(
        now=i.now,
        sunrise=i.sunrise,
        sunset=i.sunset,
        charge=charge,
        night=_night(i, _sun(i.pv, Quantile.P50)),
        best_window=_best_window(i, load),
        free_power=i.battery.soc_pct >= FREE_SOC_PCT and i.export_kw >= FREE_EXPORT_KW,
        curve=expected.points,
    )
