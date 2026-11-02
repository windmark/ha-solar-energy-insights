"""The day and the night are stepped by one engine (batch.run). It must agree,
on every result, with the original step-by-step loop kept in reference.py."""

from __future__ import annotations

import math
import random
from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.solar_energy_insights.core.battery import Battery, simulate
from tests.core.reference import simulate_steps


def days(count: int, seed: int = 7) -> list[tuple]:
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        start = datetime(
            2026, 6, 10, rng.randint(0, 23), rng.choice([0, 7, 30, 45]), tzinfo=UTC
        )
        end = start + timedelta(hours=rng.uniform(0.2, 30))
        mark = rng.choice(
            [None, start + timedelta(hours=rng.uniform(-2, 32)), end, start]
        )
        battery = Battery(
            capacity_kwh=rng.choice([5, 10, 15.2]),
            soc_pct=rng.choice([0, 3, 30, 60, 99.6, 100, 100.4]),
            floor_pct=rng.choice([0, 5, 10]),
            max_power_kw=rng.choice([1.5, 5, 10]),
            ceiling_pct=rng.choice([100, 90, 80]),
        )
        peak, house = rng.uniform(0, 12), rng.uniform(0.1, 3)

        def pv(t: datetime, peak: float = peak) -> float:
            hour = t.hour + t.minute / 60
            return (
                max(0.0, peak * math.sin(math.pi * (hour - 6) / 14))
                if 6 <= t.hour < 20
                else 0.0
            )

        def load(t: datetime, house: float = house) -> float:
            return house * (1.8 if 17 <= t.hour < 22 else 1.0)

        out.append((battery, start, end, pv, load, mark))
    return out


def close(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


@pytest.mark.parametrize(
    "day",
    days(1000),
    ids=lambda d: f"{d[1]:%H:%M}+{(d[2] - d[1]).total_seconds() / 3600:.1f}h",
)
def test_one_engine_agrees_with_the_original_loop(day: tuple) -> None:
    new, old = simulate(*day), simulate_steps(*day)

    for f in fields(new):
        a, b = getattr(new, f.name), getattr(old, f.name)
        if f.name == "points":
            assert [t for t, _ in a] == [t for t, _ in b]
            assert all(
                math.isclose(x, y, abs_tol=1e-9)
                for (_, x), (_, y) in zip(a, b, strict=True)
            )
        else:
            assert close(a, b), (f.name, a, b)
