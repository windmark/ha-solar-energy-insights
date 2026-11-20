"""Your last year again, with a bigger battery, a heat pump or one string fewer.

The recorded hours of solar and house use are replayed through the battery,
once as the house is and once with the change: more capacity, or the
electricity a heat pump would have needed in each hour's outdoor temperature.
Both runs use the same engine as the plan, so they differ only by the change.
Days the recorder lacks are filled first (see `climate.fill_year`), so the
replay covers a whole year, winter included.

The heat pump's heat comes from the degree-hour method of VDI 4710 / VDI 3807:
on a heating day (one whose mean outdoor temperature is below the heating
limit) each hour needs the heat loss times indoor less outdoor. The heat loss is
meant as the effective one, as measured from a heating bill: the sun's and the
household's free heat are already in it, which is why it is lower than the
heat-load figure of a DIN EN 12831 calculation, and why the heating limit,
not a separate gain, ends the heating season. Each hour's heat is divided by
a coefficient of performance from the outdoor temperature and the flow
temperature the heating curve asks for. It answers "about how much", not the
installer's sizing.

Without a string, each hour's solar loses that string's share of it: the
strings' power is measured before the inverter, the solar after, so the share
carries the string over without its losses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

import numpy as np
import numpy.typing as npt

from . import batch
from .batch import Floats, Slices
from .battery import Battery

# The air-source heat pump's COP against the lift from the outdoor air to the
# water it heats: the quadratic regression on manufacturers' data of Ruhnau,
# Hirth & Praktiknjo, "Time series of heat demand and heat pump efficiency for
# energy system modeling", Scientific Data 6, 189 (2019), eq. 4, itself after
# Fischer & Madani (2017). Times their 0.85 for the losses of real systems
# (defrosting, pumps, cycling), set from German field measurements.
COP_CURVE = (6.08, -0.09, 0.0005)
COP_IN_THE_FIELD = 0.85
# The regression's data cover lifts of about this range; outside it the
# parabola would turn back up.
LIFT_MIN_K, LIFT_MAX_K = 15.0, 65.0
# A datasheet's COP at A2/W35 (EN 14511: air 2 °C, water 35 °C, a 33 K lift)
# scales the curve, which is the average of the units of its time: the curve
# keeps its shape through the rated point, as a fixed exergetic efficiency
# would (DIN EN 15316-4-2, Annex C).
RATED_LIFT_K = 33.0
# Ruhnau et al. heat hot water to a constant 50 °C, from German field data.
HOT_WATER_SINK_C = 50.0
# Radiators give out heat as the excess temperature to the power 1.3 (EN 442),
# so a heating curve for a part load q of the design load sets the water
# indoor + (design flow - indoor) · q^(1/1.3). Floor heating is nearer 1.1.
RADIATOR_EXPONENT = 1.3
# A weather-compensated controller follows a damped outdoor temperature, and a
# brick house's walls answer the weather over about half a day: the heat needed
# follows the outdoor temperature through a first-order low-pass with this time
# constant. The daily total stays the same; only its hours shift.
BUILDING_TIME_CONSTANT_H = 12.0
# Hot water heated in the day's sunniest hours: the tank takes this much heat an
# hour, as a heat pump's hot-water charge does.
HOT_WATER_CHARGE_KW = 3.0
# Heating limits the card can show the yearly heat for, without asking again.
DEGREE_HOUR_LIMITS = tuple(float(c) for c in range(8, 21))
# Germany's degree-day number G20/15 (VDI 4710): indoor 20 °C, heating limit 15 °C.
G20_15 = (20.0, 15.0)
# A night: this many hours or more in a row without sun.
NIGHT_HOURS = 4
NO_SUN_KWH = 0.01
# Less than this from the grid in a night counts as without it.
NIGHT_GRID_KWH = 0.1


@dataclass(frozen=True, slots=True)
class Hour:
    """One recorded hour."""

    start: datetime
    solar_kwh: float
    house_kwh: float
    outdoor_c: float | None = None
    # Each string's energy in the hour, from its power: ((n, kWh), …).
    strings: tuple[tuple[int, float], ...] = ()
    # What the grid meters counted, to check the replay against.
    metered_import_kwh: float | None = None
    metered_export_kwh: float | None = None
    # False for an hour borrowed from another day to fill the year.
    measured: bool = True

    def string_share(self, n: int) -> float:
        """String *n*'s part of the hour's solar, 0 without string readings."""
        total = sum(max(kwh, 0.0) for _, kwh in self.strings)
        own = sum(max(kwh, 0.0) for m, kwh in self.strings if m == n)
        return own / total if total > 0 else 0.0


def _curve(lift: Floats | float) -> Floats:
    a, b, c = COP_CURVE
    x = np.clip(np.asarray(lift, dtype=float), LIFT_MIN_K, LIFT_MAX_K)
    return np.asarray(a + b * x + c * x**2)


def cop(lift_k: Floats | float, rated_a2w35: float | None = None) -> Floats:
    """An air-source heat pump's coefficient of performance in the field, for a
    lift in kelvin from the outdoor air to the water (`COP_CURVE`), scaled to a
    datasheet's COP at A2/W35 if given."""
    scale = rated_a2w35 / float(_curve(RATED_LIFT_K)) if rated_a2w35 else 1.0
    return np.asarray(COP_IN_THE_FIELD * scale * _curve(lift_k))


def damped(outdoor: Floats, hours: float = BUILDING_TIME_CONSTANT_H) -> Floats:
    """The outdoor temperature through a first-order low-pass (an exponential
    moving average) with time constant *hours*, stepped hourly:
    y_k = y_(k-1) + a (x_k - y_(k-1)), with a = 1 - exp(-1 h / tau)."""
    if hours <= 0 or not len(outdoor):
        return np.asarray(outdoor, dtype=float)
    alpha = 1 - np.exp(-1 / hours)
    out = np.empty(len(outdoor))
    y = float(outdoor[0])
    for k, x in enumerate(outdoor.tolist()):
        y += alpha * (x - y)
        out[k] = y
    return out


def _day_index(starts: Sequence[datetime]) -> npt.NDArray[np.intp]:
    """Each hour's day, as a running number from 0."""
    days = [t.date() for t in starts]
    first = {d: i for i, d in enumerate(dict.fromkeys(days))}
    return np.array([first[d] for d in days], dtype=np.intp)


def heating_days(starts: Sequence[datetime], outdoor: Floats, limit_c: float) -> Floats:
    """Whether each hour lies on a heating day: one whose mean outdoor
    temperature is below the heating limit, as degree days count them."""
    day = _day_index(starts)
    if not len(day):
        return np.zeros(0, dtype=bool)
    sums = np.bincount(day, weights=outdoor)
    counts = np.bincount(day)
    return np.asarray((sums / counts)[day] < limit_c)


def degree_hours(
    starts: Sequence[datetime], outdoor: Floats, indoor_c: float, limit_c: float
) -> float:
    """Kelvin-hours of heating: indoor less the damped outdoor temperature, over
    the hours of heating days. The heat a house needs is its effective heat loss
    times these; G20/15 is these for 20 °C indoors and a 15 °C limit, over 24."""
    on = heating_days(starts, outdoor, limit_c)
    lift = np.maximum(indoor_c - damped(outdoor), 0.0)
    return float(np.sum(np.where(on, lift, 0.0)))


@dataclass(frozen=True, slots=True)
class HeatLoad:
    """A heat pump's hours, in kWh."""

    space_heat: Floats
    water_heat: Floats
    electricity: Floats
    # The electricity of the heating rod, for the heat beyond the pump's size.
    backup: Floats
    heating_days: int


@dataclass(frozen=True, slots=True)
class HeatPump:
    heat_loss_w_per_k: float
    heating_limit_c: float = 15.0
    indoor_c: float = 20.0
    hot_water_kwh_per_day: float = 0.0
    # The heating curve: this flow temperature at this outdoor temperature.
    design_flow_c: float = 45.0
    design_outdoor_c: float = -12.0
    # The most heat it gives in an hour, in kW; above it, a heating rod adds
    # the rest at a COP of 1. None: always enough.
    capacity_kw: float | None = None
    # Hot water heated in the day's sunniest hours, not spread over the day.
    hot_water_at_noon: bool = False
    # The datasheet's COP at A2/W35; None: the published average curve.
    rated_cop_a2w35: float | None = None

    def flow_c(self, outdoor_c: Floats | float) -> Floats:
        """The flow temperature the heating curve sets at *outdoor_c*."""
        span = max(self.indoor_c - self.design_outdoor_c, 1.0)
        load = np.maximum(self.indoor_c - np.asarray(outdoor_c, dtype=float), 0.0)
        return np.asarray(
            self.indoor_c
            + (self.design_flow_c - self.indoor_c)
            * (load / span) ** (1 / RADIATOR_EXPONENT)
        )

    def space_cop(self, outdoor_c: Floats | float) -> Floats:
        """The COP heating the house: the flow temperature follows the damped
        outdoor temperature, the pump draws from the air as it is. Given one
        temperature, both are that."""
        t = np.asarray(outdoor_c, dtype=float)
        return cop(self.flow_c(t) - t, self.rated_cop_a2w35)

    def load(
        self, starts: Sequence[datetime], outdoor: Floats, sun: Floats
    ) -> HeatLoad:
        """Each hour's heat and electricity at the recorded outdoor
        temperatures, with hot water in the sunniest hours if so set."""
        count = len(starts)
        slow = damped(outdoor)
        on = heating_days(starts, outdoor, self.heating_limit_c)
        space = (
            np.where(
                on, np.maximum(self.indoor_c - slow, 0.0) * self.heat_loss_w_per_k, 0.0
            )
            / 1000
        )
        water = self._water(starts, sun, count)
        space_cop = cop(self.flow_c(slow) - outdoor, self.rated_cop_a2w35)
        water_cop = cop(HOT_WATER_SINK_C - outdoor, self.rated_cop_a2w35)
        heat = space + water
        if self.capacity_kw is None or self.capacity_kw <= 0:
            pumped = np.ones(count)
        else:
            pumped = np.minimum(1.0, self.capacity_kw / np.maximum(heat, 1e-9))
        backup = (1 - pumped) * heat
        electricity = pumped * (space / space_cop + water / water_cop) + backup
        days = int(np.unique(_day_index(starts)[on]).size)
        return HeatLoad(space, water, electricity, backup, days)

    def _water(self, starts: Sequence[datetime], sun: Floats, count: int) -> Floats:
        per_day = max(self.hot_water_kwh_per_day, 0.0)
        if not per_day or not count:
            return np.zeros(count)
        if not self.hot_water_at_noon:
            return np.full(count, per_day / 24)
        water = np.zeros(count)
        day = _day_index(starts)
        for d in np.unique(day):
            idx = np.flatnonzero(day == d)
            left = per_day * len(idx) / 24
            # Sunniest first; on a day without sun, the hours as they come.
            for i in idx[np.argsort(-sun[idx], kind="stable")]:
                if left <= 0:
                    break
                water[i] = min(HOT_WATER_CHARGE_KW, left)
                left -= water[i]
        return water


@dataclass(frozen=True, slots=True)
class Gas:
    """The heating the house has now, for what the heat pump replaces."""

    price_per_kwh: float
    # Useful heat per kWh of gas billed: about 0.9 for a condensing boiler.
    efficiency: float = 0.9
    # The gas connection's standing charge, saved once it goes.
    standing_per_year: float = 0.0


@dataclass(frozen=True, slots=True)
class Scenario:
    extra_battery_kwh: float = 0.0
    heat_pump: HeatPump | None = None
    without_string: int | None = None


@dataclass(frozen=True, slots=True)
class Hourly:
    """A replay's hours, for the months."""

    starts: tuple[datetime, ...]
    grid: Floats
    pump: Floats
    measured: Floats


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
    space_heat_kwh: float = 0.0
    water_heat_kwh: float = 0.0
    backup_kwh: float = 0.0
    heating_days: int = 0
    # Full charge-discharge cycles: what came out over what it can hold.
    battery_cycles: float = 0.0
    hourly: Hourly | None = field(default=None, compare=False, repr=False)

    @property
    def days(self) -> float:
        return self.hours / 24

    @property
    def heat_kwh(self) -> float:
        return self.space_heat_kwh + self.water_heat_kwh

    @property
    def seasonal_cop(self) -> float | None:
        """Heat out over electricity in, over the year: the seasonal
        performance factor (Jahresarbeitszahl), heating rod included."""
        return self.heat_kwh / self.heat_pump_kwh if self.heat_pump_kwh > 0 else None

    @property
    def self_sufficiency(self) -> float:
        return 1 - self.import_kwh / self.load_kwh if self.load_kwh > 0 else 0.0

    def cost(self, price: float, feed_in: float) -> float:
        return self.import_kwh * price - (self.export_kwh + self.stored_kwh) * feed_in


@dataclass(frozen=True, slots=True)
class Month:
    """One calendar month of the year with the change, in kWh."""

    month: int
    import_now_kwh: float
    import_kwh: float
    heat_pump_kwh: float
    # The heat pump's electricity that came from the roof, directly or through
    # the battery: what it added to the bill less than it used.
    heat_pump_solar_kwh: float
    measured_share: float


@dataclass(frozen=True, slots=True)
class Comparison:
    """The house as it is (`now`) against the house with every change
    (`changed`). With both a bigger battery and a heat pump, two more runs keep
    them apart: `pump_only`, the heat pump on today's battery, prices the
    battery; `battery_only`, the bigger battery without the heat pump, prices
    the heat pump. Each is the change it leaves out, so neither takes credit
    for the other."""

    now: Outcome
    changed: Outcome
    price: float
    feed_in: float
    pump_only: Outcome | None = None
    battery_only: Outcome | None = None
    # Kelvin-hours over the year for each heating limit (indoor as set), and
    # G20/15 · 24, from the same temperatures.
    degree_hours: dict[float, float] = field(default_factory=dict)
    degree_hours_g20_15: float | None = None

    @property
    def per_year(self) -> float:
        return 365 / self.now.days if self.now.hours else 0.0

    def _cost(self, o: Outcome) -> float:
        return o.cost(self.price, self.feed_in)

    @property
    def saving_per_year(self) -> float:
        """What the changes save a year, against the house as it is. A heat
        pump costs more electricity: this is then negative."""
        return (self._cost(self.now) - self._cost(self.changed)) * self.per_year

    @property
    def battery_saving_per_year(self) -> float:
        """What the bigger battery alone saves a year, with the heat pump if
        there is one."""
        base = self.pump_only or self.now
        return (self._cost(base) - self._cost(self.changed)) * self.per_year

    @property
    def heat_pump_cost_per_year(self) -> float:
        """What the heat pump adds to the electricity bill a year, on the
        battery it would have."""
        base = self.battery_only or self.now
        return (self._cost(self.changed) - self._cost(base)) * self.per_year

    @property
    def heat_pump_solar_share(self) -> float | None:
        """The share of the heat pump's electricity that came from the roof,
        directly or through the battery, not from the grid."""
        extra = self.changed.heat_pump_kwh
        if extra <= 0:
            return None
        base = self.battery_only or self.now
        from_grid = self.changed.import_kwh - base.import_kwh
        return min(max(1 - from_grid / extra, 0.0), 1.0)

    def gas_cost_per_year(self, gas: Gas) -> float | None:
        """What the same heat would cost from gas a year."""
        heat = self.changed.heat_kwh
        if heat <= 0 or gas.efficiency <= 0:
            return None
        return heat / gas.efficiency * gas.price_per_kwh * self.per_year + max(
            gas.standing_per_year, 0.0
        )

    def months(self) -> list[Month]:
        """The year by calendar month, January first: months not in the
        hours are left out."""
        now, changed = self.now.hourly, self.changed.hourly
        if now is None or changed is None or not changed.starts:
            return []
        base = (self.battery_only or self.now).hourly or now
        month = np.array([t.month for t in changed.starts])
        out = []
        for m in range(1, 13):
            sel = month == m
            if not sel.any():
                continue
            pump = float(changed.pump[sel].sum())
            added = float(changed.grid[sel].sum() - base.grid[sel].sum())
            out.append(
                Month(
                    m,
                    float(now.grid[sel].sum()),
                    float(changed.grid[sel].sum()),
                    pump,
                    min(max(pump - added, 0.0), pump),
                    float(changed.measured[sel].mean()),
                )
            )
        return out


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
    hours: Sequence[Hour],
    battery: Battery,
    heat_pump: HeatPump | None = None,
    *,
    without_string: int | None = None,
    export_cap_kw: float | None = None,
) -> Outcome:
    """Step the battery through the recorded hours, with a heat pump's load,
    without a string's solar, and with the grid taking no more than the cap."""
    count = len(hours)
    if not count:
        return Outcome(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
    sun = np.array(
        [
            max(h.solar_kwh, 0.0)
            * (1 - (h.string_share(without_string) if without_string else 0.0))
            for h in hours
        ]
    )
    house = np.array([max(h.house_kwh, 0.0) for h in hours])
    starts = tuple(h.start for h in hours)
    heat = (
        heat_pump.load(starts, np.array(_temperatures(hours)), sun)
        if heat_pump is not None
        else None
    )
    pump = heat.electricity if heat is not None else np.zeros(count)
    load = house + pump
    slices = Slices(starts, np.ones(count), np.ones(count, dtype=bool))
    runs = batch.run(
        battery, [battery.soc_pct], slices, sun, load[np.newaxis, :], record=True
    )
    assert runs.levels_pct is not None
    level = runs.levels_pct[0] * battery.capacity_kwh / 100
    grid, export = _flows(battery, sun, load, level)
    if export_cap_kw is not None:
        # Hourly steps: above the cap the inverter holds the sun back.
        export = np.minimum(export, max(export_cap_kw, 0.0))
    nights, lasted = _nights(sun, grid)
    usable = battery.capacity_kwh * (battery.ceiling_pct - battery.floor_pct) / 100
    out_of_cells = float(np.maximum(level[:-1] - level[1:], 0.0).sum())
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
        space_heat_kwh=float(heat.space_heat.sum()) if heat else 0.0,
        water_heat_kwh=float(heat.water_heat.sum()) if heat else 0.0,
        backup_kwh=float(heat.backup.sum()) if heat else 0.0,
        heating_days=heat.heating_days if heat else 0,
        battery_cycles=out_of_cells / usable if usable > 0 else 0.0,
        hourly=Hourly(
            starts, grid, pump, np.array([h.measured for h in hours], dtype=float)
        ),
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
    """How many nights there were, and how many got by without the grid. The
    dark hours the replay starts in are not a whole night, and the battery
    starts them empty: they don't count."""
    nights = lasted = 0
    run_hours, run_grid = 0, 0.0
    seen_sun = False
    for kwh, bought in zip(sun.tolist(), grid.tolist(), strict=True):
        if kwh < NO_SUN_KWH:
            run_hours += 1
            run_grid += bought
            continue
        if seen_sun and run_hours >= NIGHT_HOURS:
            nights += 1
            lasted += run_grid < NIGHT_GRID_KWH
        seen_sun = True
        run_hours, run_grid = 0, 0.0
    return nights, lasted


def compare(
    hours: Sequence[Hour],
    battery: Battery,
    scenario: Scenario,
    price: float,
    feed_in: float,
    export_cap_kw: float | None = None,
) -> Comparison:
    """The house as it is against the house with *scenario*, all starting
    empty: a bigger battery must not start with more to give."""
    start = replace(battery, soc_pct=battery.floor_pct)
    bigger = replace(
        start, capacity_kwh=start.capacity_kwh + max(scenario.extra_battery_kwh, 0.0)
    )
    pump = scenario.heat_pump

    def run(b: Battery, p: HeatPump | None) -> Outcome:
        return replay(
            hours,
            b,
            p,
            without_string=scenario.without_string,
            export_cap_kw=export_cap_kw,
        )

    now = replay(hours, start, export_cap_kw=export_cap_kw)
    changed = run(bigger, pump)
    both = pump is not None and scenario.extra_battery_kwh > 0
    degrees: dict[float, float] = {}
    g20_15 = None
    if pump is not None and hours:
        starts = [h.start for h in hours]
        temps = np.array(_temperatures(hours))
        degrees = {
            limit: degree_hours(starts, temps, pump.indoor_c, limit)
            for limit in DEGREE_HOUR_LIMITS
        }
        g20_15 = degree_hours(starts, temps, *G20_15)
    return Comparison(
        now,
        changed,
        price,
        feed_in,
        pump_only=run(start, pump) if both else None,
        battery_only=run(bigger, None) if both else None,
        degree_hours=degrees,
        degree_hours_g20_15=g20_15,
    )


@dataclass(frozen=True, slots=True)
class StringWorth:
    """What one string added over the hours with string readings: its solar,
    and the replay with and without it, beside what the meters counted."""

    string: int
    made_kwh: float
    comparison: Comparison
    metered_hours: int
    metered_import_kwh: float
    metered_export_kwh: float

    @property
    def value(self) -> float:
        """Money the string saved over these hours: the bill without it less
        the bill with it."""
        c = self.comparison
        return c.changed.cost(c.price, c.feed_in) - c.now.cost(c.price, c.feed_in)


def string_worth(
    hours: Sequence[Hour],
    battery: Battery,
    string: int,
    price: float,
    feed_in: float,
    export_cap_kw: float | None = None,
) -> StringWorth:
    """The year replayed with and without *string*, over the hours that have
    string readings: before them its part is unknown."""
    known = [h for h in hours if h.strings]
    comparison = compare(
        known,
        battery,
        Scenario(without_string=string),
        price,
        feed_in,
        export_cap_kw,
    )
    metered = [h for h in known if h.metered_import_kwh is not None]
    return StringWorth(
        string=string,
        made_kwh=sum(max(h.solar_kwh, 0.0) * h.string_share(string) for h in known),
        comparison=comparison,
        metered_hours=len(metered),
        metered_import_kwh=sum(h.metered_import_kwh or 0.0 for h in metered),
        metered_export_kwh=sum(h.metered_export_kwh or 0.0 for h in metered),
    )
