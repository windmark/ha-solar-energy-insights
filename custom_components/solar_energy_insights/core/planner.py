"""The plan: what the battery, the night and the sun will do from now on.

A port of the dashboard's ``glance``: four short answers that change with the time
of day, plus the structured facts behind them so automations can act on them.

Phases:
- day: sunrise to sunset; today's battery, tonight, appliances, production so far
- evening: sunset to 21:00; how today went, tonight, the cost of a run tonight
- late: 21:00 to sunrise; tonight, the next day's battery, appliances, forecast
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from enum import StrEnum

from .battery import Battery, Rate, Simulation, simulate
from .forecast import PvForecast, Quantile
from .load import Evening, LoadModel

LATE_HOUR = 21
NIGHT_HOUR = 22
EVENING_DECIDED_HOUR = 15
# Battery at least this full and exporting at least this much = free power.
FREE_SOC_PCT = 97.0
FREE_EXPORT_KW = 1.5
# Spare solar from which a half hour counts towards an appliance window.
WINDOW_SURPLUS_KW = 1.0
# How far apart two outcomes must be before both are mentioned.
DISTINCT = timedelta(minutes=20)
# A capacity used only to step the model when the inverter reports none and the
# battery is empty, where the answers do not depend on it.
PLACEHOLDER_CAPACITY_KWH = 10.0


class Phase(StrEnum):
    DAY = "day"
    EVENING = "evening"
    LATE = "late"


class Tone(StrEnum):
    GOOD = "good"
    WARN = "warn"
    BAD = "bad"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class ApplianceLoad:
    """An appliance someone might start now, as one run of average power."""

    name: str
    kwh: float
    hours: float

    def rate(self, start: datetime) -> Rate:
        def kw(t: datetime) -> float:
            hours = (t - start).total_seconds() / 3600
            return self.kwh / self.hours if 0 <= hours < self.hours else 0.0

        return kw


@dataclass(frozen=True, slots=True)
class PlanInputs:
    now: datetime
    tz: tzinfo
    sunrise: datetime
    sunset: datetime
    battery: Battery
    pv: PvForecast
    load: LoadModel
    solar_kw: float = 0.0
    house_kw: float = 0.0
    export_kw: float = 0.0
    yield_today_kwh: float | None = None
    forecast_today_kwh: float | None = None
    # Solcast's daily totals from tomorrow on.
    forecast_next_days: Sequence[float] = ()
    full_since: datetime | None = None
    soc_max_today: float | None = None
    cooked_tonight: bool = False
    appliances: Sequence[ApplianceLoad] = ()
    price_eur_kwh: float = 0.30
    capacity_known: bool = True


@dataclass(frozen=True, slots=True)
class Line:
    key: str
    label: str
    text: str
    tone: Tone
    icon: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime
    kwh: float


@dataclass(frozen=True, slots=True)
class Plan:
    """The answers, and the numbers they come from."""

    now: datetime
    phase: Phase
    grid_mode: bool
    headline: str
    subline: str
    tone: Tone
    lines: tuple[Line, ...]
    # Today's (or, at night, the next day's) charging.
    full_at: datetime | None
    peak_pct: float
    peak_at: datetime
    sunset_pct: float | None
    # The night ahead, for the evening most likely tonight.
    lasts_night: bool
    lasts_night_any_evening: bool
    reserve_at: datetime | None
    night_load_kwh: float
    night_grid_kwh: float
    night_spare_kwh: float
    # Using power now.
    free_power: bool
    free_until: datetime | None
    best_window: Window | None
    sun_covers_house_at: datetime | None
    curve: tuple[tuple[datetime, float], ...] = field(repr=False)


def _hm(t: datetime, tz: tzinfo) -> str:
    return t.astimezone(tz).strftime("%H:%M")


def _kwh(v: float) -> str:
    return f"{v:.0f}" if v >= 20 else f"{v:.1f}"


def _slot_start(t: datetime) -> datetime:
    """Return the start of the half hour holding *t*."""
    return t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)


@dataclass(slots=True)
class _Ctx:
    i: PlanInputs
    battery: Battery
    mix: Rate

    def run(
        self, end: datetime, load: Rate | None = None, mark: datetime | None = None
    ) -> Simulation:
        return simulate(
            self.battery,
            self.i.now,
            end,
            self.i.pv.kw_at,
            load or self.mix,
            mark_at=mark,
        )

    def rate(self, kind: Evening) -> Rate:
        load, tz = self.i.load, self.i.tz
        return lambda t: load.rate(t, tz, kind)

    def hm(self, t: datetime) -> str:
        return _hm(t, self.i.tz)


def _window(ctx: _Ctx, start: datetime, end: datetime) -> Window | None:
    """The longest run of half hours where solar beats the house by 1 kW."""
    best: Window | None = None
    cur_start: datetime | None = None
    cur_end = start
    cur_sum = 0.0
    t = _slot_start(start)
    while t < end:
        spare = ctx.i.pv.kw_at(t) - ctx.mix(t)
        if spare >= WINDOW_SURPLUS_KW:
            if cur_start is None:
                cur_start, cur_sum = t, 0.0
            cur_end = t + timedelta(minutes=30)
            cur_sum += spare * 0.5
        elif cur_start is not None:
            if best is None or cur_sum > best.kwh:
                best = Window(cur_start, cur_end, cur_sum)
            cur_start = None
        t += timedelta(minutes=30)
    if cur_start is not None and (best is None or cur_sum > best.kwh):
        best = Window(cur_start, cur_end, cur_sum)
    return best


@dataclass(frozen=True, slots=True)
class _Milestones:
    w500: datetime | None
    kw1: datetime | None
    cover: datetime | None
    peak_kw: float


def _milestones(ctx: _Ctx, start: datetime, load: Rate) -> _Milestones:
    """When the sun first gives 500 W, 1 kW, and when it covers the house.

    Solcast gives half-hour averages: each is taken as the value at the middle of
    its half hour and the crossing is interpolated, to the nearest 5 minutes.
    """
    points: list[tuple[datetime, float, float]] = []
    end = ctx.i.sunset if ctx.i.sunset > start else start + timedelta(days=1)
    t = _slot_start(start)
    while t < end:
        mid = t + timedelta(minutes=15)
        kw = ctx.i.pv.kw_at(mid)
        points.append((mid, kw, kw - load(mid)))
        t += timedelta(minutes=30)
    floor = max(start, ctx.i.now)

    def rounded(t: datetime) -> datetime:
        ts = round(t.timestamp() / 300) * 300
        return max(datetime.fromtimestamp(ts, t.tzinfo), floor)

    def cross(index: int, level: float) -> datetime | None:
        for n, point in enumerate(points):
            if point[index] < level:
                continue
            if n == 0:
                return rounded(point[0])
            a, b = points[n - 1], point
            share = (level - a[index]) / (b[index] - a[index])
            return rounded(a[0] + (b[0] - a[0]) * share)
        return None

    return _Milestones(
        cross(1, 0.5), cross(1, 1.0), cross(2, 0.0), max((p[1] for p in points), default=0)
    )


def plan(inputs: PlanInputs) -> Plan:  # noqa: PLR0912, PLR0915 - one answer per branch
    """Work out the plan for the moment in *inputs*."""
    i = inputs
    tz, now = i.tz, i.now
    battery = i.battery
    capacity_unknown = not i.capacity_known or battery.capacity_kwh <= 0
    if capacity_unknown:
        battery = replace(battery, capacity_kwh=PLACEHOLDER_CAPACITY_KWH)
    mix_load, tz_ = i.load, tz
    ctx = _Ctx(i, battery, lambda t: mix_load.rate(t, tz_, Evening.MIX))
    hm = ctx.hm
    local = now.astimezone(tz)
    hour = local.hour

    is_day = i.sunset < i.sunrise
    pre_dawn = i.sunrise.astimezone(tz).date() == local.date()
    phase = (
        Phase.DAY
        if is_day
        else Phase.LATE
        if hour >= LATE_HOUR or pre_dawn
        else Phase.EVENING
    )
    soc = battery.soc_pct

    # ── Tonight: which evenings are still possible ──
    night_time = hour >= NIGHT_HOUR or pre_dawn
    cooked = i.cooked_tonight and (night_time or hour >= EVENING_DECIDED_HOUR)
    kinds = [
        kind
        for kind in (Evening.QUIET, Evening.COOK)
        if (kind is Evening.QUIET and not cooked)
        or (kind is Evening.COOK and not (not cooked and night_time))
    ]
    nights = {
        kind: ctx.run(i.sunrise, ctx.rate(kind), i.sunset if is_day else None)
        for kind in kinds
    }
    lasting = [kind for kind in kinds if nights[kind].lasts]
    if len(lasting) == len(kinds):
        spare = min(nights[kind].spare_kwh for kind in kinds)
        either = " either way" if len(kinds) > 1 else ""
        night = Line(
            "night",
            "Tonight",
            f"Lasts the night{either} · {_kwh(spare)} kWh spare",
            Tone.GOOD,
            "mdi:weather-night",
        )
        night_short = f"Lasts the night · {_kwh(spare)} kWh spare"
    elif lasting:
        cook_end = nights[Evening.COOK].reserve_at
        assert cook_end is not None
        night = Line(
            "night",
            "Tonight",
            f"Lasts if you don’t cook · cooking: grid ~{hm(cook_end)}",
            Tone.WARN,
            "mdi:weather-night",
        )
        night_short = "Lasts if you don’t cook"
    else:
        ends = sorted(
            (nights[kind] for kind in kinds),
            key=lambda sim: sim.reserve_at or i.sunrise,
        )
        worst, best = ends[0], ends[-1]
        assert worst.reserve_at is not None and best.reserve_at is not None
        spread = best.reserve_at - worst.reserve_at >= DISTINCT
        night = Line(
            "night",
            "Tonight",
            f"Reserve ~{hm(worst.reserve_at)} · ~{_kwh(worst.grid_kwh)} kWh from the grid",
            Tone.BAD,
            "mdi:weather-night",
        )
        night_short = f"Grid from ~{hm(best.reserve_at if spread else worst.reserve_at)}" + (
            f" (~{hm(worst.reserve_at)} if you cook)" if spread else ""
        )

    # ── Battery ──
    day_run = ctx.run(i.sunset, mark=i.sunset)
    sunset_pct = day_run.mark_pct
    if phase is Phase.DAY:
        if battery.is_full:
            since = (
                f"Full since {hm(i.full_since)}"
                if i.full_since and i.full_since.astimezone(tz).date() == local.date()
                else "Full"
            )
            more = f" · ~{_kwh(day_run.export_kwh)} kWh more export" if day_run.export_kwh > 0.3 else ""
            battery_line = Line("battery", "Battery today", since + more, Tone.GOOD, "mdi:battery-check")
            battery_short = since
        elif day_run.full_at:
            then = f" · then ~{_kwh(day_run.export_kwh)} kWh export" if day_run.export_kwh > 0.3 else ""
            battery_line = Line(
                "battery", "Battery today", f"Full ~{hm(day_run.full_at)}{then}", Tone.GOOD, "mdi:battery-charging-high"
            )
            battery_short = f"Full ~{hm(day_run.full_at)}"
        elif day_run.peak_pct > soc + 1:
            at_sunset = sunset_pct if sunset_pct is not None else day_run.peak_pct
            battery_line = Line(
                "battery",
                "Battery today",
                f"Peaks at {day_run.peak_pct:.0f}% ~{hm(day_run.peak_at)} · {at_sunset:.0f}% at sunset",
                Tone.WARN,
                "mdi:battery-charging-50",
            )
            battery_short = f"Peaks {day_run.peak_pct:.0f}% ~{hm(day_run.peak_at)}"
        else:
            at_sunset = sunset_pct if sunset_pct is not None else day_run.end_pct
            battery_line = Line(
                "battery",
                "Battery today",
                f"No more charging today · {at_sunset:.0f}% at sunset",
                Tone.WARN,
                "mdi:battery-minus",
            )
            battery_short = "No more charging today"
    elif phase is Phase.EVENING:
        full = i.full_since is not None and i.full_since.astimezone(tz).date() == local.date()
        if full:
            assert i.full_since is not None
            battery_line = Line(
                "battery",
                "Battery today",
                f"Got full at {hm(i.full_since)} · now {soc:.0f}%",
                Tone.GOOD,
                "mdi:battery-check",
            )
            battery_short = f"Got full at {hm(i.full_since)}"
        else:
            peaked = f"Peaked at {i.soc_max_today:.0f}% today · " if i.soc_max_today is not None else ""
            battery_line = Line(
                "battery", "Battery today", f"{peaked}now {soc:.0f}%", Tone.NEUTRAL, "mdi:battery-50"
            )
            battery_short = (
                f"Peaked at {i.soc_max_today:.0f}%" if i.soc_max_today is not None else f"Now {soc:.0f}%"
            )
    else:
        day_word = "Today" if pre_dawn else "Tomorrow"
        if day_run.full_at:
            then = f" · then ~{_kwh(day_run.export_kwh)} kWh export" if day_run.export_kwh > 0.3 else ""
            battery_line = Line(
                "battery",
                f"Battery {day_word.lower()}",
                f"Full ~{hm(day_run.full_at)}{then}",
                Tone.GOOD,
                "mdi:battery-charging-high",
            )
            battery_short = f"{day_word} full by {hm(day_run.full_at)}"
        else:
            at = f" ~{hm(day_run.peak_at)}" if day_run.peak_pct > 5 else ""
            battery_line = Line(
                "battery",
                f"Battery {day_word.lower()}",
                f"Peaks at {day_run.peak_pct:.0f}%{at} · won’t fill",
                Tone.WARN,
                "mdi:battery-charging-50",
            )
            battery_short = f"{day_word} peaks at {day_run.peak_pct:.0f}%"
    if capacity_unknown:
        battery_line = Line(
            "battery",
            battery_line.label,
            "Battery capacity not reported · forecast resumes once it is",
            Tone.NEUTRAL,
            "mdi:battery-unknown",
        )
        battery_short = "Battery data unavailable"

    # ── Appliances ──
    free_power = soc >= FREE_SOC_PCT and i.export_kw >= FREE_EXPORT_KW
    free_until: datetime | None = None
    best_window: Window | None = None
    if free_power:
        t = _slot_start(now) + timedelta(minutes=30)
        while t < i.sunset:
            if i.pv.kw_at(t) - ctx.mix(t) < FREE_EXPORT_KW:
                free_until = t
                break
            t += timedelta(minutes=30)
        until = f" until ~{hm(free_until)}" if free_until else ""
        appliances = Line(
            "appliances",
            "Right now",
            f"Free power · {i.export_kw:.1f} kW spare{until}",
            Tone.GOOD,
            "mdi:washing-machine",
        )
        appliances_short = f"Good time for heavy loads{until}"
    elif phase is Phase.DAY:
        best_window = _window(ctx, now, i.sunset)
        if best_window is None:
            appliances = Line("appliances", "Appliances", "Little spare sun", Tone.NEUTRAL, "mdi:washing-machine")
            appliances_short = "Little spare sun today"
        elif best_window.start <= now:
            appliances = Line(
                "appliances",
                "Appliances",
                f"Good time now · until ~{hm(best_window.end)}",
                Tone.GOOD,
                "mdi:washing-machine",
            )
            appliances_short = f"Run appliances until ~{hm(best_window.end)}"
        else:
            appliances = Line(
                "appliances",
                "Appliances",
                f"Best {hm(best_window.start)}–{hm(best_window.end)} · most spare sun",
                Tone.NEUTRAL,
                "mdi:washing-machine",
            )
            appliances_short = f"Run appliances {hm(best_window.start)}–{hm(best_window.end)}"
    elif phase is Phase.EVENING and i.appliances:
        failing: list[tuple[ApplianceLoad, datetime]] = []
        for appliance in i.appliances:
            extra = appliance.rate(now)
            run = ctx.run(i.sunrise, lambda t, extra=extra: ctx.mix(t) + extra(t))
            if run.reserve_at is not None:
                failing.append((appliance, run.reserve_at))
        if failing:
            name, at = failing[0][0].name, failing[0][1]
            appliances = Line(
                "appliances",
                f"{name} tonight",
                f"Grid from ~{hm(at)} · if you run it tonight",
                Tone.WARN,
                "mdi:dishwasher",
            )
            appliances_short = f"{name} tonight → grid {hm(at)}"
        else:
            names = " and ".join(a.name for a in i.appliances[:2])
            appliances = Line(
                "appliances",
                "Appliances tonight",
                "Covered · battery still lasts the night",
                Tone.GOOD,
                "mdi:dishwasher",
            )
            appliances_short = f"{names} tonight is fine"
    else:
        best_window = _window(ctx, i.sunrise, i.sunset)
        day_word = "today" if pre_dawn else "tomorrow"
        if best_window:
            appliances = Line(
                "appliances",
                f"Appliances {day_word}",
                f"Best {hm(best_window.start)}–{hm(best_window.end)} · most spare sun",
                Tone.NEUTRAL,
                "mdi:washing-machine",
            )
            appliances_short = f"Appliances {day_word} {hm(best_window.start)}–{hm(best_window.end)}"
        else:
            appliances = Line(
                "appliances", f"Appliances {day_word}", "Little spare sun", Tone.NEUTRAL, "mdi:washing-machine"
            )
            appliances_short = f"Little spare sun {day_word}"

    # ── Production ──
    yield_today = i.yield_today_kwh or 0.0
    fc_today = i.forecast_today_kwh
    if phase is Phase.DAY:
        start_of_day = local.replace(hour=0, minute=0, second=0, microsecond=0)
        so_far = i.pv.energy(start_of_day, now)
        ratio = yield_today / so_far if so_far > 0.3 else None
        how = (
            ""
            if ratio is None
            else "ahead of forecast"
            if ratio > 1.1
            else "behind forecast"
            if ratio < 0.9
            else "on track"
        )
        of = _kwh(fc_today) if fc_today is not None else "?"
        production = Line(
            "production",
            "Production",
            f"{_kwh(yield_today)} of {of} kWh so far" + (f" · {how}" if how else ""),
            Tone.NEUTRAL if ratio is None else Tone.WARN if ratio < 0.9 else Tone.GOOD,
            "mdi:solar-power-variant",
        )
    elif phase is Phase.EVENING:
        vs = ""
        if fc_today and fc_today > 0.3:
            pct = (yield_today / fc_today - 1) * 100
            vs = f" · forecast was {_kwh(fc_today)} ({'+' if pct >= 0 else '−'}{abs(pct):.0f}%)"
        production = Line(
            "production", "Production today", f"{_kwh(yield_today)} kWh{vs}", Tone.NEUTRAL, "mdi:solar-power-variant"
        )
    else:
        days = ([fc_today] if pre_dawn and fc_today is not None else []) + list(i.forecast_next_days)
        day_word = "today" if pre_dawn else "tomorrow"
        if days:
            first = days[0]
            how = ""
            if len(days) >= 3:
                avg = sum(days) / len(days)
                how = (
                    f"best of the next {len(days)} days"
                    if first >= max(days)
                    else "dull compared with the week"
                    if first < avg * 0.7
                    else "better than average"
                    if first > avg * 1.15
                    else "about average"
                )
            text = f"{_kwh(first)} kWh forecast" + (f" · {how}" if how else "")
        else:
            text = "No forecast"
        production = Line("production", f"Sun {day_word}", text, Tone.NEUTRAL, "mdi:solar-power-variant")

    # ── At night, or on the grid in the morning: the night card ──
    now_load, now_pv = ctx.mix(now), i.pv.kw_at(now)
    at_floor = soc <= battery.floor_pct + 1
    grid_mode = at_floor and now_pv < now_load and (not is_day or now_pv < 1)
    kind_now = kinds[0] if len(kinds) == 1 else Evening.MIX
    load_now = ctx.rate(kind_now)
    eur = lambda kwh: f"≈ €{kwh * i.price_eur_kwh:.2f}"  # noqa: E731
    sun_word = "today" if is_day or pre_dawn else "tomorrow"
    lines: tuple[Line, ...]
    cover_at: datetime | None = None

    def sun_row(ms: _Milestones) -> Line:
        parts = [f"{label} ~{hm(t)}" for label, t in (("500 W", ms.w500), ("1 kW", ms.kw1)) if t]
        total = i.forecast_today_kwh if sun_word == "today" else (
            i.forecast_next_days[0] if i.forecast_next_days else None
        )
        covers = f"covers the house from ~{hm(ms.cover)}" if ms.cover else "won’t cover the house"
        detail = " · ".join([covers] + ([f"{_kwh(total)} kWh in total"] if total is not None else []))
        if parts:
            return Line(
                "sun", f"Sun {sun_word}", " · ".join(parts), Tone.GOOD if ms.kw1 else Tone.WARN,
                "mdi:solar-power-variant", detail,
            )
        return Line(
            "sun", f"Sun {sun_word}", "Under 500 W all day", Tone.WARN, "mdi:solar-power-variant",
            f"peak ~{ms.peak_kw:.1f} kW" + (f" · {_kwh(total)} kWh in total" if total is not None else ""),
        )

    headline_tone: Tone
    if not is_day:
        tonight = ctx.run(i.sunrise, load_now)
        from_battery = max(tonight.load_kwh - tonight.grid_kwh, 0.0)
        if grid_mode or from_battery < 0.05:
            tone, detail = Tone.BAD, f"all from the grid · {eur(tonight.grid_kwh)}"
        elif len(kinds) == 2 and nights[Evening.QUIET].lasts and not nights[Evening.COOK].lasts:
            cook_at = nights[Evening.COOK].reserve_at
            assert cook_at is not None
            tone, detail = Tone.WARN, f"from the battery if you don’t cook · cooking: grid from ~{hm(cook_at)}"
        elif tonight.lasts:
            tone, detail = Tone.GOOD, f"all from the battery · ~{_kwh(tonight.spare_kwh)} kWh left at sunrise"
        else:
            assert tonight.reserve_at is not None
            tone, detail = (
                Tone.WARN,
                f"{_kwh(from_battery)} from the battery · ~{_kwh(tonight.grid_kwh)} from the grid after ~{hm(tonight.reserve_at)}",
            )
        night_row = Line(
            "night",
            f"Until sunrise at {hm(i.sunrise)}",
            f"~{_kwh(tonight.load_kwh)} kWh",
            tone,
            "mdi:transmission-tower-import" if tone is Tone.BAD else "mdi:weather-night",
            detail,
        )
        ms = _milestones(ctx, i.sunrise, load_now)
        cover_at = ms.cover
        rows = [night_row, sun_row(ms), replace(battery_line, label=f"Battery {sun_word}")]
        if phase is Phase.EVENING and not grid_mode:
            rows.append(appliances)
        lines = tuple(rows)
    elif grid_mode:
        ms = _milestones(ctx, now, load_now)
        cover_at = ms.cover
        rows = [sun_row(ms)]
        if ms.cover:
            to_cover = ctx.run(ms.cover, load_now)
            rows.append(
                Line(
                    "grid",
                    "Grid until the sun takes over",
                    f"~{_kwh(to_cover.grid_kwh)} kWh",
                    Tone.NEUTRAL,
                    "mdi:transmission-tower",
                    f"until ~{hm(ms.cover)} · {eur(to_cover.grid_kwh)}",
                )
            )
        rows.append(replace(battery_line, label="Battery today"))
        lines = tuple(rows)
    else:
        cover_at = _milestones(ctx, now, load_now).cover
        lines = (battery_line, night, appliances, production)

    # ── Headline ──
    if grid_mode:
        headline = f"On the grid until ~{hm(cover_at)}" if cover_at else "On the grid · battery empty"
        headline_tone = Tone.BAD
        if not is_day:
            subline = f"~{_kwh(ctx.run(i.sunrise, load_now).load_kwh)} kWh tonight · sunrise {hm(i.sunrise)}"
        else:
            ms = _milestones(ctx, now, load_now)
            subline = (
                f"500 W ~{hm(ms.w500)}" + (f" · 1 kW ~{hm(ms.kw1)}" if ms.kw1 else "")
                if ms.w500
                else "Little sun today"
            )
    elif phase is Phase.DAY:
        if free_power:
            headline, headline_tone = f"Free power now · {i.export_kw:.1f} kW spare", Tone.GOOD
        else:
            headline, headline_tone = battery_short, battery_line.tone
        subline = f"{night_short} tonight" if night.tone is Tone.BAD else appliances_short
    elif phase is Phase.EVENING:
        headline, headline_tone, subline = night_short, night.tone, appliances_short
    else:
        headline, headline_tone, subline = night_short, night.tone, battery_short

    # ── Facts for entities and automations ──
    mix_night = ctx.run(i.sunrise, ctx.mix)
    likely = mix_night if len(kinds) > 1 else nights[kinds[0]]
    full_at = (i.full_since or now) if battery.is_full and phase is Phase.DAY else day_run.full_at
    return Plan(
        now=now,
        phase=phase,
        grid_mode=grid_mode,
        headline=headline,
        subline=subline,
        tone=headline_tone,
        lines=lines,
        full_at=full_at,
        peak_pct=day_run.peak_pct,
        peak_at=day_run.peak_at,
        sunset_pct=sunset_pct,
        lasts_night=likely.lasts,
        lasts_night_any_evening=len(lasting) == len(kinds),
        reserve_at=likely.reserve_at,
        night_load_kwh=likely.load_kwh,
        night_grid_kwh=likely.grid_kwh,
        night_spare_kwh=likely.spare_kwh,
        free_power=free_power,
        free_until=free_until,
        best_window=best_window,
        sun_covers_house_at=cover_at,
        curve=day_run.points,
    )


def simulate_with(
    inputs: PlanInputs,
    until: datetime,
    extra: Sequence[ApplianceLoad] = (),
    kind: Evening = Evening.MIX,
    quantile: Quantile = Quantile.P50,
) -> Simulation:
    """Run the battery to *until* with some appliances started now."""
    tz, load, now = inputs.tz, inputs.load, inputs.now
    extras: list[Callable[[datetime], float]] = [a.rate(now) for a in extra]

    def rate(t: datetime) -> float:
        return load.rate(t, tz, kind) + sum(e(t) for e in extras)

    pv = inputs.pv

    def sun(t: datetime) -> float:
        return pv.kw_at(t, quantile)

    return simulate(inputs.battery, now, until, sun, rate, mark_at=inputs.sunset)


def battery_from(
    capacity_kwh: float | None,
    soc_pct: float | None,
    reserve_pct: float | None,
    min_floor_pct: float,
    max_power_kw: float,
    round_trip: float,
) -> Battery:
    """Build the battery from readings that may be missing.

    The floor is the reserve in force, never below the lowest state of charge the
    inverter discharges to by itself.
    """
    return Battery(
        capacity_kwh=capacity_kwh or 0.0,
        # An unreported state of charge is treated as empty: the safe answer.
        soc_pct=soc_pct if soc_pct is not None else 0.0,
        floor_pct=max(reserve_pct or 0.0, min_floor_pct),
        max_power_kw=max_power_kw,
        round_trip=round_trip,
    )
