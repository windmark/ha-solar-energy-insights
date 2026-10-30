"""Would a bigger battery pay? Replay the real hours with extra capacity.

The extra battery only sees what really happened: it charges from energy that was
actually exported and discharges into energy that was actually bought. The
existing battery stays exactly as it was, so there is nothing to calibrate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo

# The extra battery keeps this much in reserve, like the real one.
MIN_SOC_PCT = 5.0


@dataclass(frozen=True, slots=True)
class Hour:
    start: datetime
    imp: float
    exp: float


@dataclass(frozen=True, slots=True)
class UpgradeValue:
    extra_kwh: float
    value_eur: float
    kwh_shifted: float
    # Value per local month ("YYYY-MM") over the replayed period.
    months: dict[str, float]
    per_year_eur: float
    months_measured: int

    def payback_years(self, cost_eur: float) -> float | None:
        return cost_eur / self.per_year_eur if self.per_year_eur > 0 else None


def replay(
    hours: Sequence[Hour], extra_kwh: float, round_trip: float, max_kw: float
) -> list[tuple[float, float]]:
    """Return each hour's (import, export) with *extra_kwh* more battery."""
    eta = math.sqrt(round_trip)
    usable = extra_kwh * (1 - MIN_SOC_PCT / 100)
    level = 0.0
    out: list[tuple[float, float]] = []
    for hour in hours:
        # Purchases are covered first: within the same hour this is conservative.
        discharge = min(hour.imp, max_kw, level * eta)
        level -= discharge / eta
        charge = min(hour.exp, max_kw, max(usable - level, 0.0) / eta)
        level += charge * eta
        out.append((hour.imp - discharge, hour.exp - charge))
    return out


def _month(t: datetime, tz: tzinfo) -> str:
    return t.astimezone(tz).strftime("%Y-%m")


def evaluate(
    hours: Sequence[Hour],
    extra_kwh: float,
    *,
    price_eur_kwh: float,
    feed_in_eur_kwh: float,
    round_trip: float,
    max_kw: float,
    tz: tzinfo,
) -> UpgradeValue:
    """Value the extra capacity over the replayed hours, and per year.

    The yearly figure averages the months seen so far; with fewer than twelve it is
    an estimate that leans towards the seasons already lived.
    """
    replayed = replay(hours, extra_kwh, round_trip, max_kw)
    months: dict[str, float] = {}
    hours_in: dict[str, int] = {}
    value = shifted = 0.0
    for hour, (imp, exp) in zip(hours, replayed, strict=True):
        bought_less, sold_less = hour.imp - imp, hour.exp - exp
        gain = bought_less * price_eur_kwh - sold_less * feed_in_eur_kwh
        key = _month(hour.start, tz)
        months[key] = months.get(key, 0.0) + gain
        hours_in[key] = hours_in.get(key, 0) + 1
        value += gain
        shifted += bought_less
    # Months with at least ten days of data, each scaled to a whole month.
    full = {
        key: months[key] / hours_in[key] * 730 for key in months if hours_in[key] >= 240
    }
    per_year = sum(full.values()) / len(full) * 12 if full else 0.0
    return UpgradeValue(
        extra_kwh=extra_kwh,
        value_eur=round(value, 2),
        kwh_shifted=round(shifted, 2),
        months={key: round(v, 2) for key, v in months.items()},
        per_year_eur=round(per_year, 2),
        months_measured=len(full),
    )


def ceiling_per_year(
    hours: Sequence[Hour],
    *,
    price_eur_kwh: float,
    feed_in_eur_kwh: float,
    round_trip: float,
    tz: tzinfo,
) -> float:
    """The most any added battery could earn per year: every kWh bought, as far as
    there was export in the same month to fill it, minus the feed-in given up."""
    imp: dict[str, float] = {}
    exp: dict[str, float] = {}
    count: dict[str, int] = {}
    for hour in hours:
        key = _month(hour.start, tz)
        imp[key] = imp.get(key, 0.0) + hour.imp
        exp[key] = exp.get(key, 0.0) + hour.exp
        count[key] = count.get(key, 0) + 1
    margin = price_eur_kwh - feed_in_eur_kwh / round_trip
    per_month = [
        min(imp[key], exp[key] * round_trip) * margin / count[key] * 730
        for key in imp
        if count[key] >= 240
    ]
    return max(sum(per_month) / len(per_month) * 12, 0.0) if per_month else 0.0
