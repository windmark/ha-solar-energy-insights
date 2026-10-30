"""The house's own load, learned per hour of the week and per kind of evening.

Appliances with a meter are subtracted and hours with an unmetered one running are
skipped before anything is learned, so the model is the house alone and every
appliance is added explicitly when a plan asks for it.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Any

HOURS_PER_WEEK = 168
# About the last five weeks of each hour of the week.
WEEK_ALPHA = 0.2
# About the last four evenings of each kind.
EVENING_ALPHA = 0.25
# The most a house plausibly uses in one hour; more is a counter glitch.
MAX_HOURLY_KWH = 10.0
# Until a cooking evening is learned, add this much between 17:00 and 20:00.
COOK_DEFAULT_KWH = 1.5
COOK_DEFAULT_HOURS = (17, 18, 19)
# Fallback load when nothing at all is learned yet.
DEFAULT_KW = 0.4
# Evenings of each kind before the cooking share is trusted.
MIN_EVENINGS_FOR_SHARE = 5
# An evening cycle runs from noon to noon, so a night belongs to its evening.
CYCLE_START_HOUR = 12


class Evening(StrEnum):
    COOK = "cook"
    QUIET = "quiet"
    MIX = "mix"


def cycle_day(t: datetime, tz: tzinfo) -> date:
    """Return the evening a moment belongs to: noon to noon the next day."""
    local = t.astimezone(tz)
    return local.date() if local.hour >= CYCLE_START_HOUR else local.date() - timedelta(1)


def _ok(value: float | None) -> bool:
    return value is not None and 0 <= value <= MAX_HOURLY_KWH


def _blend(old: float | None, new: float, alpha: float) -> float:
    return round(new if old is None else old * (1 - alpha) + new * alpha, 4)


@dataclass(slots=True)
class LoadModel:
    """kWh per clock hour: one per hour of the week, and one per evening kind."""

    week: list[float | None] = field(default_factory=lambda: [None] * HOURS_PER_WEEK)
    cook: list[float | None] = field(default_factory=lambda: [None] * 24)
    quiet: list[float | None] = field(default_factory=lambda: [None] * 24)
    n_cook: int = 0
    n_quiet: int = 0
    # One-step-ahead error of the weekly profile, summed over scored hours.
    abs_err: float = 0.0
    scored_hours: int = 0

    @staticmethod
    def _week_index(local: datetime) -> int:
        return local.weekday() * 24 + local.hour

    def observe_hour(self, start: datetime, kwh: float, tz: tzinfo) -> None:
        """Learn one hour of house load, after scoring what was expected."""
        if not _ok(kwh):
            return
        local = start.astimezone(tz)
        index = self._week_index(local)
        expected = self.week[index]
        if expected is not None:
            self.abs_err += abs(kwh - expected)
            self.scored_hours += 1
        self.week[index] = _blend(expected, kwh, WEEK_ALPHA)

    def observe_evening(self, hours: Sequence[float | None], cooked: bool) -> None:
        """Fold one noon-to-noon cycle, indexed by clock hour, into its kind."""
        profile = self.cook if cooked else self.quiet
        if not any(_ok(value) for value in hours):
            return
        for hour, value in enumerate(hours):
            if value is not None and _ok(value):
                profile[hour] = _blend(profile[hour], value, EVENING_ALPHA)
        if cooked:
            self.n_cook += 1
        else:
            self.n_quiet += 1

    @property
    def mae_kwh(self) -> float | None:
        """Mean absolute error of the hourly forecast, once anything is scored."""
        return self.abs_err / self.scored_hours if self.scored_hours else None

    @property
    def cook_share(self) -> float | None:
        """How often the house cooks in the evening, once enough are seen."""
        total = self.n_cook + self.n_quiet
        return self.n_cook / total if total >= MIN_EVENINGS_FOR_SHARE else None

    def learned(self, kind: Evening) -> bool:
        if kind is Evening.COOK:
            return self.n_cook > 0
        if kind is Evening.QUIET:
            return self.n_quiet > 0
        return self.n_cook > 0 and self.n_quiet > 0

    def _fallback(self) -> float:
        known = [value for value in self.week if value is not None and _ok(value)]
        return statistics.median(known) if len(known) >= 6 else DEFAULT_KW

    def _one(self, local: datetime, kind: Evening) -> float:
        profile = self.cook if kind is Evening.COOK else self.quiet
        if self.learned(kind) and (value := profile[local.hour]) is not None:
            return value
        week = self.week[self._week_index(local)]
        value = week if week is not None else self._fallback()
        if (
            kind is Evening.COOK
            and not self.learned(kind)
            and local.hour in COOK_DEFAULT_HOURS
        ):
            value += COOK_DEFAULT_KWH / len(COOK_DEFAULT_HOURS)
        return value

    def rate(self, t: datetime, tz: tzinfo, kind: Evening = Evening.MIX) -> float:
        """Return the expected house load in kW at *t*."""
        local = t.astimezone(tz)
        if kind is not Evening.MIX:
            return self._one(local, kind)
        share = self.cook_share
        p = 0.5 if share is None else share
        return p * self._one(local, Evening.COOK) + (1 - p) * self._one(
            local, Evening.QUIET
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "week": self.week,
            "cook": self.cook,
            "quiet": self.quiet,
            "n_cook": self.n_cook,
            "n_quiet": self.n_quiet,
            "abs_err": self.abs_err,
            "scored_hours": self.scored_hours,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> LoadModel:
        model = cls()
        if not data:
            return model
        for name, size in (("week", HOURS_PER_WEEK), ("cook", 24), ("quiet", 24)):
            values = data.get(name)
            if isinstance(values, list) and len(values) == size:
                setattr(model, name, [None if v is None else float(v) for v in values])
        model.n_cook = int(data.get("n_cook", 0))
        model.n_quiet = int(data.get("n_quiet", 0))
        model.abs_err = float(data.get("abs_err", 0.0))
        model.scored_hours = int(data.get("scored_hours", 0))
        return model


# Evening hours searched for cooking, as clock hours [start, end).
COOK_WINDOW = (16, 22)


def detect_cooking(
    samples: Sequence[tuple[datetime, float]], tz: tzinfo, threshold_kw: float
) -> bool:
    """Return whether the house drew *threshold_kw* beyond its appliances.

    *samples* are 5-minute average kW of the house minus metered appliances; one
    block over the threshold inside the evening window counts as cooking.
    """
    return any(
        COOK_WINDOW[0] <= start.astimezone(tz).hour < COOK_WINDOW[1]
        and kw >= threshold_kw
        for start, kw in samples
    )
