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

from custom_components.solar_energy_insights.const import BACKTEST_EVENINGS
from custom_components.solar_energy_insights.core.backtest import (
    Calibration,
    calibrate,
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


def replay_calibration(path: Path) -> Calibration:
    """What the recent evenings taught, as they stood then: the
    hidden load, and the scores."""
    data = load(path)
    inputs = from_snapshot(data)
    # The evenings the integration scores now: a file may hold more.
    evenings = evenings_from_snapshot(data)[-BACKTEST_EVENINGS:]
    return calibrate(
        inputs.use,
        inputs.tz,
        evenings,
        inputs.battery,
        inputs.nights,
    )


def fixtures() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))
