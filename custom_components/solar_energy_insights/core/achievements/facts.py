"""One closed day, reduced to the facts every rule reads.

A fact a home cannot supply stays None, and rules needing it skip that day.
Facts are built from the recorder's hourly statistics (and five-minute ones for
recent days), plus the integration's saved forecast and answers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from ..backtest import Evening

type Hourly = Mapping[tuple[date, int], float]

# A day needs this many hours of house use to count.
MIN_HOURS = 20
# An hour importing less than this counts as off the grid, unless the inverter's
# own background draw has been learned (see background_draw).
OFF_GRID_KWH = 0.02
# The most an hour's import can be and still be the inverter's own draw.
MAX_DRAW_KWH = 0.1
# Hours with the battery this far above its reserve could cover the house: what
# still came from the grid then is the inverter's own draw.
PLENTY_ABOVE_FLOOR_PCT = 10.0
# Hours needed before the draw is learned, and the share of them it covers.
MIN_DRAW_HOURS = 48
DRAW_QUANTILE = 0.95
DRAW_STEP_KWH = 0.0001
# Within this many points of the charge limit counts as full.
FULL_MARGIN_PCT = 1.0
# Under this many kWh imported in a day is a grid-free day.
GRID_FREE_KWH = 0.05


@dataclass(frozen=True, slots=True)
class DayFacts:
    day: date
    house_kwh: float
    solar_kwh: float | None = None
    # The best hour's yield: how big the array is, seen from its output.
    solar_best_hour_kwh: float | None = None
    import_kwh: float | None = None
    export_kwh: float | None = None
    offgrid_hours: float | None = None
    # Off the grid from midnight on, up to midnight, and the longest run in the day.
    offgrid_head: float | None = None
    offgrid_tail: float | None = None
    offgrid_run: float | None = None
    # The inverter's own draw allowed for the day, kWh (the tolerance per hour
    # times the day's hours).
    background_kwh: float | None = None
    # The longest stretch off the grid ending this day, hours, across midnight
    # and across days: derived from the days before, not stored.
    offgrid_stretch: float | None = None
    soc_max: float | None = None
    soc_min: float | None = None
    full_at: datetime | None = None
    # The night that began this evening.
    night_lasted: bool | None = None
    night_spare_kwh: float | None = None
    # Solcast's forecast for the day, as saved that morning.
    forecast_kwh: float | None = None
    peak_kw: float | None = None
    # What the plan said: "full at" that morning, the night outlook that evening.
    said_full_at: datetime | None = None
    said_full: bool | None = None
    said_outlook: str | None = None

    # ── Derived ──

    @property
    def own_import_kwh(self) -> float | None:
        """What came from the grid beyond the inverter's own draw."""
        if self.import_kwh is None:
            return None
        return max(self.import_kwh - (self.background_kwh or 0.0), 0.0)

    @property
    def self_sufficiency(self) -> float | None:
        own = self.own_import_kwh
        if own is None or self.house_kwh <= 0:
            return None
        return max(0.0, min(1.0, 1 - own / self.house_kwh))

    @property
    def kept_at_home(self) -> float | None:
        if self.solar_kwh is None or self.export_kwh is None or self.solar_kwh <= 0.1:
            return None
        return max(0.0, min(1.0, 1 - self.export_kwh / self.solar_kwh))

    @property
    def grid_free(self) -> bool | None:
        own = self.own_import_kwh
        return None if own is None else own < GRID_FREE_KWH

    @property
    def full(self) -> bool | None:
        return None if self.soc_max is None else self.full_at is not None

    @property
    def beat_forecast(self) -> bool | None:
        if self.solar_kwh is None or not self.forecast_kwh:
            return None
        return self.solar_kwh > self.forecast_kwh

    def full_minutes(self, tz: tzinfo) -> int | None:
        if self.full_at is None:
            return None
        local = self.full_at.astimezone(tz)
        return local.hour * 60 + local.minute

    # ── Storage ──

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("offgrid_stretch")  # derived from the days before
        for key, value in out.items():
            if isinstance(value, (date, datetime)):
                out[key] = value.isoformat()
        return {k: v for k, v in out.items() if v is not None}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DayFacts:
        values: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            if f.name == "day":
                value = date.fromisoformat(value)
            elif f.name in ("full_at", "said_full_at") and value is not None:
                value = datetime.fromisoformat(value)
            values[f.name] = value
        return cls(**values)


@dataclass(frozen=True, slots=True)
class Series:
    """The recorder's hourly statistics, keyed by local date and hour."""

    house: Hourly
    solar: Hourly | None = None
    imported: Hourly | None = None
    exported: Hourly | None = None
    soc_max: Hourly | None = None
    soc_min: Hourly | None = None
    # The highest solar power in each hour, kW.
    solar_peak: Hourly | None = None
    # The highest state of charge per five minutes, where still kept.
    soc_fine: Mapping[datetime, float] | None = None


def _day(
    series: Hourly | None, day: date, min_hours: int = MIN_HOURS
) -> list[float] | None:
    if series is None:
        return None
    values = [series[(day, h)] for h in range(24) if (day, h) in series]
    return values if values and len(values) >= min_hours else None


def build(
    day: date,
    tz: tzinfo,
    s: Series,
    *,
    ceiling_pct: float = 100.0,
    floor_pct: float = 5.0,
    capacity_kwh: float = 10.0,
    evening: Evening | None = None,
    forecast_kwh: float | None = None,
    said: Mapping[str, Any] | None = None,
    min_hours: int = MIN_HOURS,
    offgrid_kwh: float = OFF_GRID_KWH,
) -> DayFacts | None:
    """The facts of one closed day, or None if the house's use is mostly missing.

    With a lower *min_hours*, the day so far: for today's live score.
    """
    house = _day(s.house, day, min_hours)
    if house is None:
        return None
    solar = _day(s.solar, day, min_hours)
    imported = _day(s.imported, day, min_hours)
    exported = _day(s.exported, day, min_hours)
    highs, lows = _day(s.soc_max, day, min_hours), _day(s.soc_min, day, min_hours)
    full_pct = ceiling_pct - FULL_MARGIN_PCT
    full_at = None
    if highs is not None:
        fine = sorted(
            (t, v)
            for t, v in (s.soc_fine or {}).items()
            if t.astimezone(tz).date() == day
        )
        if fine:
            full_at = next((t for t, v in fine if v >= full_pct), None)
        else:
            hour = next(
                (
                    h
                    for h in range(24)
                    if s.soc_max and s.soc_max.get((day, h), 0) >= full_pct
                ),
                None,
            )
            if hour is not None:
                full_at = datetime(day.year, day.month, day.day, hour, tzinfo=tz)
    peaks = _day(s.solar_peak, day, min_hours)
    said = said or {}
    said_full_at = said.get("full_at")
    return DayFacts(
        day=day,
        house_kwh=round(sum(house), 3),
        solar_kwh=None if solar is None else round(sum(solar), 3),
        solar_best_hour_kwh=None if solar is None else round(max(solar), 3),
        import_kwh=None if imported is None else round(sum(imported), 3),
        export_kwh=None if exported is None else round(sum(exported), 3),
        offgrid_hours=None
        if imported is None
        else float(sum(1 for v in imported if v < offgrid_kwh)),
        **_offgrid_runs(s.imported, day, offgrid_kwh, imported is not None),
        background_kwh=None
        if imported is None
        else round(len(imported) * _allowed(offgrid_kwh), 3),
        soc_max=None if highs is None else max(highs),
        soc_min=None if lows is None else min(lows),
        full_at=full_at,
        night_lasted=None if evening is None else evening.lowest_soc > floor_pct + 1,
        night_spare_kwh=None
        if evening is None
        else round(max(evening.lowest_soc - floor_pct, 0) / 100 * capacity_kwh, 3),
        forecast_kwh=forecast_kwh,
        peak_kw=None if peaks is None else round(max(peaks), 3),
        said_full_at=datetime.fromisoformat(said_full_at) if said_full_at else None,
        said_full=None if "full_at" not in said else said_full_at is not None,
        said_outlook=said.get("outlook"),
    )


def yesterday_of(day: date) -> date:
    return day - timedelta(days=1)


def _allowed(offgrid_kwh: float) -> float:
    """The inverter's own draw allowed per hour: none at the plain threshold."""
    return offgrid_kwh if offgrid_kwh > OFF_GRID_KWH else 0.0


def _offgrid_runs(
    imported: Hourly | None, day: date, offgrid_kwh: float, known: bool
) -> dict[str, float | None]:
    """Hours off the grid from midnight on, up to midnight, and the longest run
    in the day. A missing hour breaks a run."""
    if not known or imported is None:
        return {"offgrid_head": None, "offgrid_tail": None, "offgrid_run": None}
    off = [(day, h) in imported and imported[(day, h)] < offgrid_kwh for h in range(24)]
    head = next((h for h, o in enumerate(off) if not o), 24)
    tail = next((h for h, o in enumerate(reversed(off)) if not o), 24)
    run = best = 0
    for o in off:
        run = run + 1 if o else 0
        best = max(best, run)
    return {
        "offgrid_head": float(head),
        "offgrid_tail": float(tail),
        "offgrid_run": float(best),
    }


def background_draw(
    imported: Hourly | None, soc_min: Hourly | None, floor_pct: float
) -> tuple[float, int]:
    """The inverter's own draw from the grid, kWh per hour, learned from hours
    when the battery was well above its reserve and could cover the house; and
    how many such hours there were. The plain threshold until there are enough,
    and never more than MAX_DRAW_KWH, so real grid use is never excused."""
    if not imported or not soc_min:
        return OFF_GRID_KWH, 0
    seen = sorted(
        max(kwh, 0.0)
        for key, kwh in imported.items()
        if soc_min.get(key, 0.0) >= floor_pct + PLENTY_ABOVE_FLOOR_PCT
    )
    if len(seen) < MIN_DRAW_HOURS:
        return OFF_GRID_KWH, len(seen)
    high = seen[min(int(DRAW_QUANTILE * len(seen)), len(seen) - 1)]
    # Rounded up and one step (0.1 Wh) above: an hour at the learned draw
    # counts as off the grid, whatever the rounding noise in the statistics.
    above = math.ceil(high * 10_000 - 1e-6) / 10_000 + DRAW_STEP_KWH
    return round(min(max(above, OFF_GRID_KWH), MAX_DRAW_KWH), 4), len(seen)


def with_stretches(days: list[DayFacts]) -> list[DayFacts]:
    """Each day with the longest stretch off the grid ending in it, carried
    across midnight and across days. A missing day breaks a stretch."""
    out, carry, before = [], 0.0, None
    for f in days:
        if before is not None and (f.day - before).days != 1:
            carry = 0.0
        if f.offgrid_head is None or f.offgrid_tail is None or f.offgrid_run is None:
            out.append(replace(f, offgrid_stretch=None))
            carry = 0.0
        elif f.offgrid_run >= 24:
            carry += 24
            out.append(replace(f, offgrid_stretch=carry))
        else:
            out.append(
                replace(f, offgrid_stretch=max(carry + f.offgrid_head, f.offgrid_run))
            )
            carry = f.offgrid_tail
        before = f.day
    return out
