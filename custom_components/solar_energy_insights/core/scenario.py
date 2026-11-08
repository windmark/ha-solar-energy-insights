"""Your last year again, with a bigger battery or a heat pump.

The recorded hours of solar and house use are replayed through the battery,
once as the house is and once with the change: more capacity, or the heat a
heat pump would have needed in each hour's outdoor temperature. Both runs use
the same engine as the plan, so they differ only by the change.

The heat pump is a simple model: the heat the house loses below its heating
limit (heat loss in W/K times indoor less outdoor), plus hot water spread over
the day, each divided by a coefficient of performance that falls as it gets
colder. It answers "about how much", not the installer's sizing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

import numpy as np

from . import batch
from .batch import Floats, Slices
from .battery import Battery

# Space heating: a typical air-to-water pump at low flow temperatures, about
# 3 at -7 °C and 4.2 at 7 °C, held within these bounds.
COP_AT_2C = 3.8
COP_PER_K = 0.09
COP_MIN, COP_MAX = 2.0, 5.5
# Hot water is heated hotter, so less efficiently.
HOT_WATER_COP = 2.8
# A night: this many hours or more in a row without sun.
NIGHT_HOURS = 4
NO_SUN_KWH = 0.01
# Less than this from the grid in a night counts as without it.
NIGHT_GRID_KWH = 0.1
START_PCT = 50.0


@dataclass(frozen=True, slots=True)
class Hour:
    """One recorded hour."""

    start: datetime
    solar_kwh: float
    house_kwh: float
    outdoor_c: float | None = None


@dataclass(frozen=True, slots=True)
class HeatPump:
    heat_loss_w_per_k: float
    heating_limit_c: float = 15.0
    indoor_c: float = 20.0
    hot_water_kwh_per_day: float = 0.0

    def cop(self, outdoor_c: float) -> float:
        return min(max(COP_AT_2C + COP_PER_K * (outdoor_c - 2.0), COP_MIN), COP_MAX)

    def kwh(self, outdoor_c: float) -> float:
        """Electricity for one hour at *outdoor_c*."""
        heating = 0.0
        if outdoor_c < self.heating_limit_c:
            heat_kw = self.heat_loss_w_per_k * (self.indoor_c - outdoor_c) / 1000
            heating = max(heat_kw, 0.0) / self.cop(outdoor_c)
        return heating + self.hot_water_kwh_per_day / 24 / HOT_WATER_COP


@dataclass(frozen=True, slots=True)
class Scenario:
    extra_battery_kwh: float = 0.0
    heat_pump: HeatPump | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    """A replay of the recorded hours, in kWh."""

    hours: int
    load_kwh: float
    solar_kwh: float
    import_kwh: float
    export_kwh: float
    heat_pump_kwh: float
    nights: int
    nights_off_grid: int
    # What the battery holds at the end more than at the start, as it would
    # come out: a run must not look cheaper for leaving it emptier.
    stored_kwh: float = 0.0

    @property
    def days(self) -> float:
        return self.hours / 24

    @property
    def self_sufficiency(self) -> float:
        return 1 - self.import_kwh / self.load_kwh if self.load_kwh > 0 else 0.0

    def cost(self, price: float, feed_in: float) -> float:
        return self.import_kwh * price - (self.export_kwh + self.stored_kwh) * feed_in


@dataclass(frozen=True, slots=True)
class Comparison:
    now: Outcome
    changed: Outcome
    price: float
    feed_in: float

    @property
    def per_year(self) -> float:
        return 365 / self.now.days if self.now.hours else 0.0

    @property
    def saving_per_year(self) -> float:
        """What the change saves a year, against the house as it is. A heat
        pump costs more electricity: this is then negative."""
        now = self.now.cost(self.price, self.feed_in)
        changed = self.changed.cost(self.price, self.feed_in)
        return (now - changed) * self.per_year

    @property
    def heat_pump_solar_share(self) -> float | None:
        """The share of the heat pump's electricity that came from the roof,
        directly or through the battery, not from the grid."""
        extra = self.changed.heat_pump_kwh
        if extra <= 0:
            return None
        from_grid = self.changed.import_kwh - self.now.import_kwh
        return min(max(1 - from_grid / extra, 0.0), 1.0)


def _temperatures(hours: Sequence[Hour]) -> list[float]:
    """Each hour's outdoor temperature, a missing one taken from the hour before
    (or the first known one)."""
    known = [h.outdoor_c for h in hours if h.outdoor_c is not None]
    if not known:
        msg = "A heat pump needs the outdoor temperature's history"
        raise ValueError(msg)
    last = known[0]
    out = []
    for h in hours:
        if h.outdoor_c is not None:
            last = h.outdoor_c
        out.append(last)
    return out


def replay(
    hours: Sequence[Hour], battery: Battery, heat_pump: HeatPump | None = None
) -> Outcome:
    """Step the battery through the recorded hours, with a heat pump's load."""
    count = len(hours)
    if not count:
        return Outcome(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
    sun = np.array([max(h.solar_kwh, 0.0) for h in hours])
    house = np.array([max(h.house_kwh, 0.0) for h in hours])
    pump = (
        np.array([heat_pump.kwh(t) for t in _temperatures(hours)])
        if heat_pump is not None
        else np.zeros(count)
    )
    load = house + pump
    slices = Slices(
        tuple(h.start for h in hours), np.ones(count), np.ones(count, dtype=bool)
    )
    runs = batch.run(
        battery, [battery.soc_pct], slices, sun, load[np.newaxis, :], record=True
    )
    assert runs.levels_pct is not None
    level = runs.levels_pct[0] * battery.capacity_kwh / 100
    grid, export = _flows(battery, sun, load, level)
    nights, lasted = _nights(sun, grid)
    return Outcome(
        hours=count,
        load_kwh=float(load.sum()),
        solar_kwh=float(sun.sum()),
        import_kwh=float(grid.sum()),
        export_kwh=float(export.sum()),
        heat_pump_kwh=float(pump.sum()),
        nights=nights,
        nights_off_grid=lasted,
        stored_kwh=float(level[-1] - level[0]) * battery.eta,
    )


def _flows(
    battery: Battery, sun: Floats, load: Floats, level: Floats
) -> tuple[Floats, Floats]:
    """Each hour's import and export, from the battery's level before and after:
    an hour either charges from the surplus or discharges into the shortfall."""
    net = sun - load
    stored = level[1:] - level[:-1]
    delivered = np.maximum(-stored, 0.0) * battery.eta
    taken = np.maximum(stored, 0.0) / battery.eta
    grid = np.where(net < 0, np.maximum(-net - delivered, 0.0), 0.0)
    export = np.where(net > 0, np.maximum(net - taken, 0.0), 0.0)
    return grid, export


def _nights(sun: Floats, grid: Floats) -> tuple[int, int]:
    """How many nights there were, and how many got by without the grid."""
    nights = lasted = 0
    run_hours, run_grid = 0, 0.0
    for kwh, bought in zip(sun.tolist(), grid.tolist(), strict=True):
        if kwh < NO_SUN_KWH:
            run_hours += 1
            run_grid += bought
            continue
        if run_hours >= NIGHT_HOURS:
            nights += 1
            lasted += run_grid < NIGHT_GRID_KWH
        run_hours, run_grid = 0, 0.0
    return nights, lasted


def compare(
    hours: Sequence[Hour],
    battery: Battery,
    scenario: Scenario,
    price: float,
    feed_in: float,
) -> Comparison:
    """The house as it is against the house with *scenario*, both from half
    full at the first hour."""
    start = replace(battery, soc_pct=max(START_PCT, battery.floor_pct))
    now = replay(hours, start)
    bigger = replace(
        start, capacity_kwh=start.capacity_kwh + max(scenario.extra_battery_kwh, 0.0)
    )
    changed = replay(hours, bigger, scenario.heat_pump)
    return Comparison(now, changed, price, feed_in)
