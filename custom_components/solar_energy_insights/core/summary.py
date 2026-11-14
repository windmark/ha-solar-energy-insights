"""What the dashboard says: each answer as a headline and at most two clauses.

Phases come from the plan's own sunset and sunrise, so every row agrees on
whether it is day, evening or after midnight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from typing import Any

HEAVY_EVENING_FACTOR = 1.3
MOVED_AT_LEAST = timedelta(minutes=15)
# Below this much spare, a budget "just fits"; at or below none, it is spent.
JUST_FITS_KWH = 1.0
SPENT_KWH = 0.05
MORNING_UNTIL_HOUR = 12
NIGHT_FROM_HOUR = 22

HEADLINE_MAX = 25
CLAUSE_MAX = 45


@dataclass(frozen=True, slots=True)
class Facts:
    """What the summary is made from: the plan's answers, and what the
    integration knows beside them."""

    now: datetime
    tz: tzinfo
    sun_up: bool
    soc: float | None
    asleep: bool = False
    limit: float = 100.0
    outlook: str | None = None
    replayed: int | None = None
    lasting: int | None = None
    recent_nights: int | None = None
    recent_lasted: int | None = None
    budget: float | None = None
    evening_budget: float | None = None
    usual_evening: float | None = None
    evening_used: float | None = None
    short_evenings: tuple[float, ...] = ()
    full_at: datetime | None = None
    full_cloudy: datetime | None = None
    peak: float | None = None
    peak_cloudy: float | None = None
    sunset_pct: float | None = None
    spare: float | None = None
    grid_kwh: float | None = None
    reserve_at: datetime | None = None
    heavy_at: datetime | None = None
    needed: float | None = None
    needed_for: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    window_kwh: float | None = None
    # The window's part of the surplus: what's left once the battery is full.
    window_free: float | None = None
    surplus: float | None = None
    saving: float | None = None
    exported: float | None = None
    grid_cost: float | None = None
    free: bool = False
    free_until: datetime | None = None
    changed_tonight: dict[str, Any] | None = None
    changed_full: dict[str, Any] | None = None
    this_morning: dict[str, Any] | None = None
    last_night: dict[str, Any] | None = None
    currency: str = "€"


@dataclass(frozen=True, slots=True)
class Row:
    label: str
    icon: str
    tone: str
    main: str
    detail: tuple[str, ...] = ()
    # Where a tap leads on the Solar page.
    target: str = ""
    # Before a recent change, and since when and why it changed.
    was: str | None = None
    change: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "icon": self.icon,
            "tone": self.tone,
            "main": self.main,
            "detail": list(self.detail),
            "target": self.target,
            "was": self.was,
            "change": self.change,
        }


@dataclass(frozen=True, slots=True)
class Summary:
    phase: str
    label: str
    time: str
    rows: tuple[Row, ...]
    ring: str
    home_main: str
    home_sub: str
    soc: float | None = None
    asleep: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "label": self.label,
            "time": self.time,
            "rows": [r.as_dict() for r in self.rows],
            "ring": self.ring,
            "home": {"main": self.home_main, "sub": self.home_sub},
            "soc": self.soc,
            "asleep": self.asleep,
        }


def _round(x: float) -> int:
    """Half up, as people round."""
    return math.floor(x + 0.5)


def kwh(v: float) -> str:
    return f"{v:.0f}" if v >= 10 else f"{v:.1f}"


class _Words:
    """The facts, with the small judgements every row shares."""

    def __init__(self, f: Facts) -> None:
        self.f = f
        local = f.now.astimezone(f.tz)
        self.hour = local.hour
        self.after_midnight = not f.sun_up and self.hour < MORNING_UNTIL_HOUR
        self.phase = (
            "day"
            if f.sun_up
            else "evening"
            if MORNING_UNTIL_HOUR <= self.hour < NIGHT_FROM_HOUR
            else "late"
        )
        self.full = f.soc is not None and f.soc >= f.limit - 0.5
        # The expected peak is where the battery is now: charging is over.
        self.done_charging = (
            f.sun_up
            and not self.full
            and f.full_at is None
            and f.peak is not None
            and f.soc is not None
            and f.peak <= f.soc + 0.5
        )
        self.next_day = (
            "today" if f.sun_up or self.hour < MORNING_UNTIL_HOUR else "tomorrow"
        )
        self.night_tight = f.outlook in ("borderline", "grid")
        eb = f.evening_budget
        self.live = (
            None
            if f.evening_used is None or eb is None or self.after_midnight
            else {"over": eb <= SPENT_KWH, "near": eb < JUST_FITS_KWH}
        )

    def hm(self, t: datetime | None) -> str:
        return t.astimezone(self.f.tz).strftime("%H:%M") if t else "–"

    def money(self, v: float) -> str:
        return f"{self.f.currency}{v:.2f}"

    def same_day(self, t: datetime) -> bool:
        return t.astimezone(self.f.tz).date() == self.f.now.astimezone(self.f.tz).date()

    def cloudy_note(self) -> str:
        f = self.f
        if f.full_cloudy is not None:
            return f"cloudier: ~{self.hm(f.full_cloudy)}"
        if (
            f.peak_cloudy is not None
            and f.soc is not None
            and f.peak_cloudy > f.soc + 0.5
        ):
            return f"cloudier: only {_round(f.peak_cloudy)}%"
        return "cloudier: won’t fill"

    def tonight(self) -> Row:
        f = self.f
        n, k = f.replayed, len(f.short_evenings)
        usual, eb = f.usual_evening, f.evening_budget
        heavy_n = (
            sum(1 for e in f.short_evenings if e > HEAVY_EVENING_FACTOR * usual)
            if usual is not None
            else k
        )
        ordinary = k - heavy_n
        headroom = eb - usual if eb is not None and usual is not None else None
        why = (
            ""
            if not n or not k
            else f"{k} of {n} short, all after heavy evenings"
            if ordinary == 0
            else f"{k} of {n} recent nights short"
            if 2 * k > n
            else f"{k} of {n} short, {ordinary} after ordinary evenings"
        )
        act = (
            ""
            if self.after_midnight or eb is None
            else f"even a usual evening (~{kwh(usual)} kWh) won't fit"
            if headroom is not None and usual is not None and headroom < 0
            else f"a usual evening (~{kwh(usual)} kWh) just fits"
            if headroom is not None and usual is not None and headroom < JUST_FITS_KWH
            else f"keep the evening under ~{kwh(eb)} kWh"
        )
        live = self.live
        live_text = (
            ""
            if live is None or eb is None
            else f"~{kwh(eb)} kWh left until midnight"
            if eb > SPENT_KWH
            else "evening budget spent"
        )
        cost = (
            ""
            if f.grid_kwh is None
            else f"~{kwh(f.grid_kwh)} kWh from the grid"
            + (f" ({self.money(f.grid_cost)})" if f.grid_cost else "")
        )
        spare = "" if f.spare is None else f"~{kwh(f.spare)} kWh spare at sunrise"
        history = (
            f"{f.recent_lasted} of the last {f.recent_nights} nights lasted"
            if f.recent_nights
            else ""
        )
        evening = live is not None
        if f.outlook == "grid":
            already = f.reserve_at is not None and f.reserve_at <= f.now + timedelta(
                minutes=5
            )
            if already:
                return Row(
                    "Tonight",
                    "mdi:transmission-tower-import",
                    "bad",
                    "On the grid now",
                    _two(
                        ""
                        if f.grid_kwh is None
                        else f"~{kwh(f.grid_kwh)} kWh until the sun",
                        history,
                    ),
                )
            detail = (
                (cost, "every kWh saved delays it")
                if evening
                else (cost,)
                if self.after_midnight
                else (cost, "a lighter evening delays it")
            )
            return Row(
                "Tonight",
                "mdi:transmission-tower-import",
                "bad",
                f"Grid from ~{self.hm(f.reserve_at)}",
                _two(*detail),
            )
        if f.outlook == "borderline":
            over = bool(live and live["over"])
            main = (
                (
                    f"Grid likely from ~{self.hm(f.heavy_at)}"
                    if f.heavy_at
                    else "Grid likely tonight"
                )
                if over
                else "Borderline tonight"
            )
            detail = (
                (live_text, "" if over else "stay within it to last")
                if evening
                else (
                    f"a heavy night: grid from ~{self.hm(f.heavy_at)}"
                    if f.heavy_at
                    else "",
                )
                if self.after_midnight
                else (why, act)
            )
            return Row(
                "Tonight",
                "mdi:weather-night",
                "bad" if over else "warn",
                main,
                _two(*detail),
            )
        if f.outlook == "lasts":
            tone = "warn" if live and live["near"] else "good"
            detail = (
                (live_text, spare)
                if evening
                else (
                    spare,
                    act if headroom is not None and headroom < JUST_FITS_KWH else "",
                )
            )
            return Row(
                "Tonight", "mdi:weather-night", tone, "Lasts the night", _two(*detail)
            )
        return Row(
            "Tonight",
            "mdi:weather-night",
            "neutral",
            "Learning your nights",
            ("needs a day of history",),
        )

    def drift(self) -> tuple[str, str] | None:
        """This morning's expectation, when the full time has moved since."""
        f, m = self.f, self.f.this_morning
        if not m or not f.sun_up:
            return None
        said = _when(m.get("said_full_at"))
        moved = (said is None) != (f.full_at is None) or (
            said is not None
            and f.full_at is not None
            and abs(said - f.full_at) >= MOVED_AT_LEAST
        )
        if not moved:
            return None
        solar, forecast = m.get("solar_kwh"), m.get("forecast_kwh")
        sun = None if solar is None or forecast is None else solar - forecast
        # The sun is a reason only if it points the way the full time moved.
        later = f.full_at is None or (said is not None and f.full_at > said)
        fits = sun is not None and abs(sun) >= 0.5 and (sun < 0) == later
        what = (
            f"this morning expected full ~{self.hm(said)}"
            if said
            else "this morning expected no full battery"
        )
        why = (
            f"{kwh(abs(sun))} kWh {'less' if sun < 0 else 'more'} sun than forecast"
            if fits and sun is not None
            else ""
        )
        return what, why

    def battery(self) -> Row:
        f = self.f
        drift = self.drift()
        at_sunset = (
            f"{_round(f.sunset_pct)}% at sunset"
            if f.sun_up and f.sunset_pct is not None
            else ""
        )
        if self.done_charging and f.soc is not None:
            sinks = f.sunset_pct is not None and _round(f.sunset_pct) < _round(f.soc)
            detail = (
                drift
                if drift
                else (f"{_round(f.soc)}% now", at_sunset if sinks else "")
            )
            return Row(
                "Battery",
                "mdi:battery-clock-outline",
                "warn",
                "No more charging today",
                _two(*detail),
                "battery",
            )
        if self.full:
            return Row(
                "Battery",
                "mdi:battery-check",
                "good",
                "Full now",
                _two(
                    f"free power until {self.hm(f.free_until)}"
                    if f.free and f.free_until
                    else "",
                    at_sunset,
                ),
                "battery",
            )
        if f.full_at is not None:
            tomorrow = "" if self.same_day(f.full_at) else " tomorrow"
            detail = (
                (drift[0], drift[1] or at_sunset)
                if drift
                else (self.cloudy_note(), at_sunset)
            )
            return Row(
                "Battery",
                "mdi:battery-charging-high",
                "good",
                f"Full ~{self.hm(f.full_at)}{tomorrow}",
                _two(*detail),
                "battery",
            )
        peak = "–" if f.peak is None else str(_round(f.peak))
        later = " tomorrow" if self.next_day == "tomorrow" else ""
        cloudier = (
            f"cloudier: {_round(f.peak_cloudy)}%"
            if f.peak_cloudy is not None
            and f.peak is not None
            and f.peak_cloudy < f.peak - 0.5
            else ""
        )
        detail = (
            drift
            if drift
            else (cloudier, f"charge limit {_round(f.limit)}%" if f.limit < 100 else "")
        )
        return Row(
            "Battery",
            "mdi:battery-charging-50",
            "warn",
            f"Peaks at {peak}%{later}",
            _two(*detail),
            "battery",
        )

    def battery_now(self) -> Row:
        f = self.f
        enough = (
            f.soc >= f.needed
            if f.needed is not None and f.soc is not None and f.needed_for == "now"
            else None
        )
        tone = (
            "neutral"
            if enough is None
            else "good"
            if enough
            else "bad"
            if f.outlook == "grid"
            else "warn"
        )
        level = "–" if f.soc is None else str(_round(f.soc))
        need = (
            f"a heavy night needs {_round(f.needed)}%"
            if f.needed is not None and f.needed_for == "now"
            else ""
        )
        return Row(
            "Battery",
            "mdi:home-battery-outline",
            tone,
            f"{level}% now{', asleep' if f.asleep else ''}",
            _two(need),
            "battery",
        )

    def next_battery(self, label: str) -> Row:
        f = self.f
        if f.full_at is not None:
            when = "" if self.same_day(f.full_at) else " tomorrow"
            return Row(
                label,
                "mdi:battery-charging-high",
                "good",
                f"Full ~{self.hm(f.full_at)}{when}",
                _two(self.cloudy_note()),
                "forecast",
            )
        peak = "–" if f.peak is None else str(_round(f.peak))
        later = " tomorrow" if self.next_day == "tomorrow" else ""
        return Row(
            label,
            "mdi:battery-charging-50",
            "warn",
            f"Peaks at {peak}%{later}",
            _two(self.cloudy_note()),
            "forecast",
        )

    def window_spare(self) -> str:
        """The window's spare sun in the surplus row's terms: what's left once
        the battery is full, so the two rows add up."""
        f = self.f
        if f.window_free is not None and f.surplus is not None and f.surplus >= 0.3:
            if f.window_free < 0.3:
                return "the battery takes this sun first"
            w, s = kwh(f.window_free), kwh(f.surplus)
            return (
                f"~{s} kWh to spare in it"
                if w == s
                else f"~{w} of the {s} kWh to spare"
            )
        if f.window_kwh is None:
            return ""
        return f"~{kwh(f.window_kwh)} kWh more sun than the house uses"

    def loads(self, tomorrow: bool) -> Row:
        f = self.f
        target = "forecast" if tomorrow else "today"
        if self.done_charging and f.window_start is None and not tomorrow:
            return Row(
                "Heavy loads",
                "mdi:washing-machine",
                "neutral",
                "No spare sun left today",
                _two(
                    "tonight they would run on the battery" if self.night_tight else ""
                ),
                target,
            )
        if f.free:
            until = f" until {self.hm(f.free_until)}" if f.free_until else " now"
            return Row(
                "Heavy loads",
                "mdi:white-balance-sunny",
                "good",
                f"Free power{until}",
                _two(
                    "battery full, the rest is exported",
                    "better now than this evening" if self.night_tight else "",
                ),
                target,
            )
        if f.window_start is not None:
            started = f.window_start <= f.now
            spare = self.window_spare()
            act = (
                ""
                if not self.night_tight
                else "not tonight: the battery is short"
                if tomorrow
                else "better now than this evening"
                if started
                else "run them then, not this evening"
            )
            main = (
                f"Now until {self.hm(f.window_end)}"
                if started
                else f"Best {self.hm(f.window_start)}–{self.hm(f.window_end)}"
                + (" tomorrow" if tomorrow else "")
            )
            return Row(
                "Heavy loads",
                "mdi:washing-machine",
                "good" if started else "neutral",
                main,
                _two(spare, act),
                target,
            )
        return Row(
            "Heavy loads",
            "mdi:washing-machine",
            "neutral",
            f"No spare sun {'tomorrow' if tomorrow else 'today'}",
            _two("the house and battery take it all"),
            target,
        )

    def surplus(self) -> Row:
        f = self.f
        if f.surplus is not None and f.surplus >= 0.3:
            worth = (
                f"{self.money(f.saving)} if used, {self.money(f.exported)} if sold"
                if f.saving is not None and f.exported is not None
                else ""
            )
            return Row(
                "Surplus",
                "mdi:solar-power-variant",
                "good",
                f"{kwh(f.surplus)} kWh to spare",
                _two(worth),
                "today",
            )
        if self.done_charging:
            return Row(
                "Surplus",
                "mdi:solar-power-variant",
                "neutral",
                "No surplus left today",
                (),
                "today",
            )
        return Row(
            "Surplus",
            "mdi:solar-power-variant",
            "neutral",
            "Little surplus today",
            _two("the battery takes the rest"),
            "today",
        )

    def last_night(self) -> Row | None:
        c = self.f.last_night
        if not c or self.hour >= MORNING_UNTIL_HOUR:
            return None
        right = c["said_lasts"] == c["lasted"]
        said = (
            f"21:00 said {kwh(c['said_spare_kwh'])} kWh"
            if c["said_lasts"]
            else "21:00 said it would need the grid"
        )
        if c["said_lasts"] and c["lasted"]:
            said += f", off by {kwh(abs(c['said_spare_kwh'] - c['spare_kwh']))}"
        main = (
            f"Lasted, {kwh(c['spare_kwh'])} kWh to spare"
            if c["lasted"]
            else "Needed the grid"
        )
        return Row(
            "Last night",
            "mdi:target" if right else "mdi:target-variant",
            "good" if right else "warn",
            main,
            _two(said, f"{c['right_calls']} of the last {c['calls']} calls right"),
            "sunrise",
        )

    def mark_changes(self, rows: list[Row]) -> list[Row]:
        """An answer that changed recently shows what it was, since when, and why."""
        f = self.f
        rows = list(rows)
        if f.changed_tonight:
            c, k = f.changed_tonight, 1
            was = _night_text(c["was"], self.hm)
            shown = was if was and was != rows[k].main else None
            before = c["was"].get("spare_kwh")
            moved = (
                before is not None
                and f.spare is not None
                and abs(before - f.spare) >= 0.3
            )
            extra = (
                f"spare {kwh(before)} → {kwh(f.spare)} kWh"
                if shown is None
                and moved
                and before is not None
                and f.spare is not None
                else ""
            )
            rows[k] = replace(rows[k], was=shown, change=self._chip(c, extra))
        if f.changed_full:
            # By day the battery row; at night the next day's.
            c, k = f.changed_full, 0 if self.phase == "day" else 2
            when = _when(c["was"].get("full_at"))
            was = f"Full ~{self.hm(when)}" if when else "Not full"
            shown = was if was != rows[k].main.removesuffix(" tomorrow") else None
            rows[k] = replace(rows[k], was=shown, change=self._chip(c, ""))
        return rows

    def _chip(self, c: dict[str, Any], extra: str) -> str:
        return " · ".join(
            x
            for x in (
                f"since {self.hm(_when(c.get('since')))}",
                extra,
                c.get("cause") or "",
            )
            if x
        )


def _when(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _night_text(was: dict[str, Any], hm: Any) -> str | None:
    outlook = was.get("outlook")
    if outlook == "lasts":
        return "Lasts the night"
    if outlook == "borderline":
        return "Borderline tonight"
    if outlook == "grid":
        grid_from = _when(was.get("grid_from"))
        return f"Grid from ~{hm(grid_from)}" if grid_from else "Grid tonight"
    return None


def _two(*clauses: str) -> tuple[str, ...]:
    """At most two clauses, the empty ones dropped."""
    return tuple(c for c in clauses if c)[:2]


def summarize(f: Facts) -> Summary:
    """The rows for the moment, in the order they are shown."""
    w = _Words(f)
    if w.phase == "day":
        rows = [w.battery(), w.tonight(), w.loads(False), w.surplus()]
    else:
        label = "Today" if w.next_day == "today" else "Tomorrow"
        rows = [
            w.battery_now(),
            w.tonight(),
            w.next_battery(label),
            w.loads(w.next_day == "tomorrow"),
        ]
    rows = w.mark_changes(rows)
    rows[1] = replace(rows[1], target="sunrise")
    last = w.last_night()
    if last is not None:
        rows.append(last)
    ring = {"lasts": "good", "borderline": "warn", "grid": "bad"}.get(
        f.outlook or "", "neutral"
    )
    if w.phase == "day":
        home_main, home_sub = rows[0].main, f"{rows[1].main} · {rows[2].main}"
    else:
        home_main, home_sub = rows[1].main, f"{rows[2].main} · {rows[3].main}"
    label = {"day": "Daytime", "evening": "Evening"}.get(w.phase, "Night")
    return Summary(
        phase=w.phase,
        label=label,
        time=f.now.astimezone(f.tz).strftime("%H:%M"),
        rows=tuple(rows),
        ring=ring,
        home_main=home_main,
        home_sub=home_sub,
        soc=None if f.soc is None else round(f.soc, 1),
        asleep=f.asleep,
    )
