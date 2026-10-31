"""Replay a moment saved in a diagnostics download.

    Settings → Devices & services → Solar Energy Insights → ⋮ → Download diagnostics

Save the file in tests/fixtures/ under a name that says what looked wrong. Every
file there is replayed by tests/test_replays.py and must pass the same checks as
the made-up situations. To pin down a fix, add a test that loads the file and
asserts what the answer should have been:

    inputs, result = replay(FIXTURES / "morning-heavy-night-looked-light.json")
    assert result.night.heavy.spare_kwh <= result.night.typical.spare_kwh
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from custom_components.solar_energy_insights.core.backtest import (
    Score,
    Weighting,
    choose,
    score,
)
from custom_components.solar_energy_insights.core.plan import Plan, PlanInputs, plan
from custom_components.solar_energy_insights.core.snapshot import (
    evenings_from_snapshot,
    from_snapshot,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def replay(path: Path) -> tuple[PlanInputs, Plan]:
    """The plan exactly as it was at the moment the file was saved."""
    inputs = from_snapshot(load(path))
    return inputs, plan(inputs)


def replay_scores(path: Path) -> tuple[Weighting, dict[Weighting, Score]]:
    """The backtest as it stood then: which weighting won, and each one's score."""
    data = load(path)
    inputs = from_snapshot(data)
    evenings = evenings_from_snapshot(data)
    return choose(
        lambda weighting: score(
            inputs.use, inputs.tz, evenings, inputs.battery, inputs.nights, weighting
        )
    )


def fixtures() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))
