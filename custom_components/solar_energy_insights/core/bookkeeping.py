"""One record per day, built from the completed day's statistics.

The record carries everything a scoreboard needs (self-sufficiency, whether the
battery filled, whether the forecast was beaten, whether the night was grid-free),
so achievements can be built on top without reading the recorder again.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

FULL_PCT = 99.0
# A night counts as ran out when the battery got within this of its floor.
RAN_OUT_MARGIN_PCT = 3.0
GRID_FREE_NIGHT_KWH = 0.1
SELF_SUFFICIENT_PCT = 95.0
SCORE_WEIGHTS = {
    "self_sufficiency": 40,
    "solar_kept": 30,
    "battery_used": 15,
    "performance": 15,
}


def _clamp01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


@dataclass(frozen=True, slots=True)
class DayTotals:
    """kWh over a day; None where the statistics had nothing."""

    solar: float | None = None
    house: float | None = None
    imp: float | None = None
    exp: float | None = None
    charged: float | None = None
    discharged: float | None = None

    @property
    def self_sufficiency_pct(self) -> float | None:
        """The share of the house's use not bought from the grid."""
        if self.house is None or self.house <= 0.2 or self.imp is None:
            return None
        return _clamp01(1 - self.imp / self.house) * 100

    @property
    def self_use_pct(self) -> float | None:
        """The share of solar used at home rather than exported."""
        if self.solar is None or self.solar <= 0.2 or self.exp is None:
            return None
        return _clamp01(1 - self.exp / self.solar) * 100


@dataclass(frozen=True, slots=True)
class Score:
    """The day's 0–100 score and each part's share of its weight (0–1)."""

    total: int
    parts: dict[str, float]


def score(
    totals: DayTotals,
    capacity_kwh: float | None,
    floor_pct: float,
    forecast_kwh: float | None,
) -> Score:
    """Score a day: own power, solar kept, battery put to work, roof performance.

    Performance is judged against the forecast frozen in the morning, so a later
    forecast that already saw the clouds does not flatter the day.
    """
    house, imp = totals.house or 0.0, totals.imp or 0.0
    solar, exp = totals.solar or 0.0, totals.exp or 0.0
    usable = (capacity_kwh or 0.0) * (100 - floor_pct) / 100
    parts = {
        "self_sufficiency": _clamp01(1 - imp / house) if house > 0 else 0.0,
        "solar_kept": _clamp01(1 - exp / solar) if solar > 0 else 0.0,
        "battery_used": _clamp01((totals.discharged or 0.0) / usable) if usable > 0 else 0.0,
        "performance": _clamp01(solar / forecast_kwh)
        if forecast_kwh and forecast_kwh > 0.5
        else 1.0,
    }
    total = round(sum(SCORE_WEIGHTS[k] * v for k, v in parts.items()))
    return Score(total, {k: round(v, 3) for k, v in parts.items()})


class Verdict(StrEnum):
    """Whether the battery was the right size for a day and its night."""

    RIGHT = "right"
    TOO_SMALL = "too_small"
    NEVER_FULL = "never_full"


def sizing_verdict(
    soc_max: float | None, min_after_full: float | None, floor_pct: float
) -> Verdict | None:
    """Full and lasted, full and ran out, or never full (more would not help)."""
    if soc_max is None:
        return None
    if soc_max < FULL_PCT:
        return Verdict.NEVER_FULL
    if min_after_full is not None and min_after_full <= floor_pct + RAN_OUT_MARGIN_PCT:
        return Verdict.TOO_SMALL
    return Verdict.RIGHT


@dataclass(frozen=True, slots=True)
class Forecasts:
    """Solcast's total for the day as it stood at each lead time, in kWh."""

    day_ahead: float | None = None
    morning: float | None = None
    midday: float | None = None
    final: float | None = None
    # The morning forecast per local clock hour, for learning the correction.
    morning_hourly: list[float] | None = None


@dataclass(frozen=True, slots=True)
class Night:
    """The night after a day: sunset to the next sunrise."""

    grid_kwh: float | None
    min_soc_after_full: float | None
    export_after_full_kwh: float | None
    verdict: Verdict | None


@dataclass(frozen=True, slots=True)
class DayRecord:
    day: date
    totals: DayTotals
    soc_max: float | None = None
    full_at: datetime | None = None
    peak_kw: float | None = None
    forecasts: Forecasts = field(default_factory=Forecasts)
    score: Score | None = None
    night: Night | None = None

    @property
    def beat_forecast(self) -> bool | None:
        reference = self.forecasts.morning or self.forecasts.final
        if reference is None or reference <= 0.5 or self.totals.solar is None:
            return None
        return self.totals.solar >= reference

    @property
    def flags(self) -> dict[str, bool | None]:
        """Yes/no facts per day, the raw material for streaks and badges."""
        sufficiency = self.totals.self_sufficiency_pct
        return {
            "self_sufficient": None if sufficiency is None else sufficiency >= SELF_SUFFICIENT_PCT,
            "battery_full": None if self.soc_max is None else self.soc_max >= FULL_PCT,
            "beat_forecast": self.beat_forecast,
            "grid_free_night": None
            if self.night is None or self.night.grid_kwh is None
            else self.night.grid_kwh < GRID_FREE_NIGHT_KWH,
        }

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["day"] = self.day.isoformat()
        data["full_at"] = self.full_at.isoformat() if self.full_at else None
        if self.night and self.night.verdict:
            data["night"]["verdict"] = str(self.night.verdict)
        data["self_sufficiency_pct"] = self.totals.self_sufficiency_pct
        data["self_use_pct"] = self.totals.self_use_pct
        data["flags"] = self.flags
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DayRecord:
        night = data.get("night")
        score_data = data.get("score")
        return cls(
            day=date.fromisoformat(data["day"]),
            totals=DayTotals(**data.get("totals", {})),
            soc_max=data.get("soc_max"),
            full_at=datetime.fromisoformat(data["full_at"]) if data.get("full_at") else None,
            peak_kw=data.get("peak_kw"),
            forecasts=Forecasts(**data.get("forecasts", {})),
            score=Score(**score_data) if score_data else None,
            night=Night(
                grid_kwh=night.get("grid_kwh"),
                min_soc_after_full=night.get("min_soc_after_full"),
                export_after_full_kwh=night.get("export_after_full_kwh"),
                verdict=Verdict(night["verdict"]) if night.get("verdict") else None,
            )
            if night
            else None,
        )
