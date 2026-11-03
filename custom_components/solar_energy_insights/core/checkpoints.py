"""How far the forecast can be trusted at the moments decisions are made.

The forecast keeps rewriting itself during the day, erasing what it said when
someone acted on it. So at fixed checkpoints the forecast is frozen, and each
is later judged against what the sun really did over the stretch it was about:

- 18:00 and 21:00: tomorrow, the whole day (the evening's decisions);
- 06:00: today, the whole day;
- 12:00: today from noon (the morning is already measured).

Scores per checkpoint, over a rolling window: the typical miss in kWh, the bias
(positive: the forecast promised more than came), and how often the real sun
fell inside the forecast's low-to-high range. They are also split by what the
forecast said (sunny, mixed or dull, against what a clear day could give), since
a confident sunny forecast and a mixed one deserve different trust, and set
beside the same weeks a year earlier once there is a year.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple


class Checkpoint(NamedTuple):
    name: str
    hour: int
    days_ahead: int
    from_noon: bool


CHECKPOINTS: tuple[Checkpoint, ...] = (
    Checkpoint("18:00", 18, 1, False),
    Checkpoint("21:00", 21, 1, False),
    Checkpoint("06:00", 6, 0, False),
    Checkpoint("12:00", 12, 0, True),
)
WINDOW_DAYS = 30
MIN_DAYS = 10
MIN_GROUP_DAYS = 8
LAST_YEAR_SPAN = 30
# Shares of a clear day's energy: at least SUNNY is sunny, below DULL dull.
SUNNY, DULL = 0.7, 0.35
WAITING_PAYS_CUT = 0.2


def checkpoint(name: str) -> tuple[str, int, int, bool]:
    return next(c for c in CHECKPOINTS if c[0] == name)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The forecast as it stood at a checkpoint, for the stretch it is judged on."""

    name: str
    day: date
    taken_at: datetime
    # Solcast's last refresh before the checkpoint.
    polled_at: datetime | None
    # Half hours: (start, likely, low, high) kW.
    slots: tuple[tuple[datetime, float, float, float], ...]

    def kwh(self, after: datetime | None = None) -> tuple[float, float, float]:
        """Likely, low and high energy, kWh, from *after* on if given."""
        rows = [s for s in self.slots if after is None or s[0] >= after]
        return (
            round(sum(s[1] for s in rows) / 2, 2),
            round(sum(s[2] for s in rows) / 2, 2),
            round(sum(s[3] for s in rows) / 2, 2),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "day": self.day.isoformat(),
            "taken_at": self.taken_at.isoformat(),
            "polled_at": self.polled_at.isoformat() if self.polled_at else None,
            "slots": [[t.isoformat(), p, lo, hi] for t, p, lo, hi in self.slots],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Snapshot:
        return cls(
            name=str(d["name"]),
            day=date.fromisoformat(d["day"]),
            taken_at=datetime.fromisoformat(d["taken_at"]),
            polled_at=datetime.fromisoformat(d["polled_at"])
            if d.get("polled_at")
            else None,
            slots=tuple(
                (datetime.fromisoformat(t), float(p), float(lo), float(hi))
                for t, p, lo, hi in d["slots"]
            ),
        )


@dataclass(frozen=True, slots=True)
class Judged:
    """One checkpoint's forecast for one day, against what came."""

    name: str
    day: date
    forecast_kwh: float
    low_kwh: float
    high_kwh: float
    actual_kwh: float
    group: str

    @property
    def error_kwh(self) -> float:
        """Positive: the forecast promised more than came."""
        return self.forecast_kwh - self.actual_kwh

    @property
    def in_range(self) -> bool:
        return self.low_kwh <= self.actual_kwh <= self.high_kwh


def group_of(forecast_kwh: float, clear_kwh: float) -> str:
    """What the forecast said, against what a clear day could give."""
    if clear_kwh <= 0:
        return "dull"
    share = forecast_kwh / clear_kwh
    return "sunny" if share >= SUNNY else "dull" if share < DULL else "mixed"


def judge(
    snap: Snapshot,
    actual_kwh: float,
    clear_kwh: float,
    noon: datetime | None = None,
) -> Judged:
    """A snapshot against the energy that came over the stretch it was about
    (from *noon* for the 12:00 checkpoint)."""
    likely, low, high = snap.kwh(noon)
    return Judged(
        snap.name,
        snap.day,
        likely,
        low,
        high,
        round(actual_kwh, 2),
        group_of(likely, clear_kwh),
    )


@dataclass(frozen=True, slots=True)
class Score:
    days: int
    typical_kwh: float | None
    bias_kwh: float | None
    in_range: float | None
    # The typical miss as a share of the period's typical day.
    typical_share: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "typical_kwh": self.typical_kwh,
            "bias_kwh": self.bias_kwh,
            "in_range": self.in_range,
            "typical_share": self.typical_share,
        }


def score(judged: Sequence[Judged], minimum: int = MIN_DAYS) -> Score:
    """The typical miss (median), the bias (mean), and the range's hit rate;
    nothing until there are enough days to mean something."""
    n = len(judged)
    if n < minimum:
        return Score(n, None, None, None, None)
    misses = [abs(j.error_kwh) for j in judged]
    typical = statistics.median(misses)
    day = statistics.mean(j.actual_kwh for j in judged)
    return Score(
        days=n,
        typical_kwh=round(typical, 2),
        bias_kwh=round(statistics.mean(j.error_kwh for j in judged), 2),
        in_range=round(sum(j.in_range for j in judged) / n, 2),
        typical_share=round(typical / day, 2) if day > 0 else None,
    )


def scores(judged: Sequence[Judged], today: date) -> dict[str, Any]:
    """Per checkpoint: the rolling window, its split by forecast, and the same
    weeks last year; and whether waiting for the next checkpoint pays."""
    out: dict[str, Any] = {}
    window = [j for j in judged if 0 < (today - j.day).days <= WINDOW_DAYS]
    year_ago = today - timedelta(days=365)
    last_year = [j for j in judged if abs((j.day - year_ago).days) <= LAST_YEAR_SPAN]
    for name, *_ in CHECKPOINTS:
        mine = [j for j in window if j.name == name]
        before = [j for j in last_year if j.name == name]
        out[name] = {
            **score(mine).as_dict(),
            "groups": {
                g: score([j for j in mine if j.group == g], MIN_GROUP_DAYS).as_dict()
                for g in ("sunny", "mixed", "dull")
            },
            "last_year": score(before).as_dict() if before else None,
        }
    out["waiting"] = {
        "21:00 vs 18:00": _waiting(window, "18:00", "21:00"),
        "12:00 vs 06:00": _waiting(window, "06:00", "12:00", afternoon=True),
    }
    return out


def _waiting(
    judged: Sequence[Judged], early: str, late: str, afternoon: bool = False
) -> dict[str, Any] | None:
    """On days both checkpoints were judged: the typical miss of each and
    whether waiting cut it by a fifth or more. For 06:00 against 12:00, the
    06:00 forecast is judged on the afternoon too (see the "06:00@noon" rows)."""
    first = {
        j.day: j for j in judged if j.name == (f"{early}@noon" if afternoon else early)
    }
    second = {j.day: j for j in judged if j.name == late}
    days = sorted(set(first) & set(second))
    if len(days) < MIN_DAYS:
        return {"days": len(days), "early_kwh": None, "late_kwh": None, "pays": None}
    e = statistics.median(abs(first[d].error_kwh) for d in days)
    lt = statistics.median(abs(second[d].error_kwh) for d in days)
    return {
        "days": len(days),
        "early_kwh": round(e, 2),
        "late_kwh": round(lt, 2),
        "pays": lt <= e * (1 - WAITING_PAYS_CUT),
    }
