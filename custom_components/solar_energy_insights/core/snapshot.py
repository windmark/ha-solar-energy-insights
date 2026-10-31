"""Everything a plan was made from, as JSON, and back.

Diagnostics carry a snapshot, so a moment that looked wrong on a real
dashboard can be replayed offline, exactly, as a test. The format is plain
JSON with a version, so old files stay readable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .backtest import Evening
from .battery import Battery
from .forecast import PvForecast, Slot
from .plan import PlanInputs

VERSION = 1


def _iso(t: datetime) -> str:
    return t.isoformat()


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def to_snapshot(i: PlanInputs, evenings: Sequence[Evening] = ()) -> dict[str, Any]:
    """The plan's inputs, and the evenings the night answer was scored on.

    An extra load (from the simulate action) is not part of a snapshot: it is a
    question asked of the plan, not something the plan was made from.
    """
    return {
        "version": VERSION,
        "now": _iso(i.now),
        "tz": str(i.tz),
        "sunrise": _iso(i.sunrise),
        "sunset": _iso(i.sunset),
        "battery": {
            "capacity_kwh": i.battery.capacity_kwh,
            "soc_pct": i.battery.soc_pct,
            "floor_pct": i.battery.floor_pct,
            "max_power_kw": i.battery.max_power_kw,
            "round_trip": i.battery.round_trip,
            "ceiling_pct": i.battery.ceiling_pct,
        },
        "export_kw": i.export_kw,
        "nights": i.nights,
        "standby_kw": i.standby_kw,
        "heavy_share": i.heavy_share,
        "forecast": [[_iso(s.start), s.p50, s.p10, s.p90] for s in i.pv.slots],
        # The house's use per local clock hour: [date, hour, kWh].
        "use": [[d.isoformat(), hour, kwh] for (d, hour), kwh in sorted(i.use.items())],
        "evenings": [
            [e.day.isoformat(), e.start_soc, _iso(e.sunrise), e.lowest_soc]
            for e in evenings
        ],
    }


def _payload(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept a snapshot, our diagnostics, or a whole diagnostics download."""
    for key in ("data", "replay"):
        if key in data and isinstance(data[key], Mapping):
            return _payload(data[key])
    return data


def from_snapshot(data: Mapping[str, Any]) -> PlanInputs:
    """Rebuild the plan's inputs, so plan() gives the same answers again."""
    s = _payload(data)
    version = s.get("version")
    if version != VERSION:
        msg = f"Unsupported snapshot version {version!r}"
        raise ValueError(msg)
    return PlanInputs(
        now=_time(s["now"]),
        tz=ZoneInfo(s["tz"]),
        sunrise=_time(s["sunrise"]),
        sunset=_time(s["sunset"]),
        battery=Battery(**s["battery"]),
        pv=PvForecast(
            Slot(_time(start), p50, p10, p90) for start, p50, p10, p90 in s["forecast"]
        ),
        use={
            (date.fromisoformat(d), int(hour)): float(kwh) for d, hour, kwh in s["use"]
        },
        nights=int(s["nights"]),
        export_kw=float(s["export_kw"]),
        # Absent in files saved before the hidden load was learned.
        standby_kw=float(s.get("standby_kw", 0.0)),
        heavy_share=float(s.get("heavy_share", 0.8)),
    )


def evenings_from_snapshot(data: Mapping[str, Any]) -> list[Evening]:
    """The evenings the night answer was scored on, for replaying the backtest."""
    return [
        Evening(date.fromisoformat(d), float(start), _time(sunrise), float(lowest))
        for d, start, sunrise, lowest in _payload(data).get("evenings", [])
    ]
