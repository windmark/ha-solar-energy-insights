"""A correction to Solcast for this roof, learned from its own mornings.

Each morning's forecast is saved, then compared hour by hour with what the
panels actually made. Per clock hour, the correction is the ratio of what came
to what was forecast, pulled towards 1 (no correction) by a prior worth a
couple of kWh, so a few odd days cannot swing it. It is switched on only when
it beats the raw forecast on days it was not learned from.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, tzinfo

import numpy as np

from .batch import Floats
from .forecast import PvForecast

# How strongly each hour's ratio is pulled towards 1, in kWh of agreement.
PRIOR_KWH = 2.0
# The correction stays within these bounds.
LIMITS = (0.5, 1.5)
# Days with both a saved forecast and actual production, before it may be used.
MIN_DAYS = 14
# It must cut the error on left-out days by this share to be used.
MIN_GAIN = 0.10


@dataclass(frozen=True, slots=True)
class Correction:
    """Factors per local clock hour, and whether they have earned their place."""

    factors: Floats
    active: bool
    days: int
    raw_error_kwh: float | None
    corrected_error_kwh: float | None

    @classmethod
    def none(cls, days: int = 0) -> Correction:
        return cls(np.ones(24), False, days, None, None)

    @property
    def improvement_pct(self) -> float | None:
        if not self.raw_error_kwh or self.corrected_error_kwh is None:
            return None
        return (1 - self.corrected_error_kwh / self.raw_error_kwh) * 100

    def apply(self, forecast: PvForecast, tz: tzinfo) -> PvForecast:
        if not self.active:
            return forecast

        def factor(start: datetime) -> float:
            return float(self.factors[start.astimezone(tz).hour])

        return forecast.scaled(factor)


def _factors(forecast: Floats, actual: Floats) -> Floats:
    """Per clock hour, from (days, 24) arrays of kWh."""
    ratio = (actual.sum(axis=0) + PRIOR_KWH) / (forecast.sum(axis=0) + PRIOR_KWH)
    return np.asarray(np.clip(ratio, *LIMITS), dtype=float)


def learn(
    forecasts: Mapping[date, Floats], actual: Mapping[date, Floats]
) -> Correction:
    """Learn from days with both a morning forecast and actual production, each
    as 24 hourly kWh, and judge it on each day left out in turn."""
    days = sorted(set(forecasts) & set(actual))
    if len(days) < MIN_DAYS:
        return Correction.none(len(days))
    f = np.array([forecasts[d] for d in days], dtype=float)
    a = np.array([actual[d] for d in days], dtype=float)
    raw = np.abs(f - a).sum(axis=1)
    held_out = np.empty(len(days))
    for k in range(len(days)):
        others = np.arange(len(days)) != k
        held_out[k] = np.abs(f[k] * _factors(f[others], a[others]) - a[k]).sum()
    raw_error, corrected_error = float(raw.mean()), float(held_out.mean())
    active = corrected_error < raw_error * (1 - MIN_GAIN)
    return Correction(
        factors=_factors(f, a),
        active=active,
        days=len(days),
        raw_error_kwh=raw_error,
        corrected_error_kwh=corrected_error,
    )
