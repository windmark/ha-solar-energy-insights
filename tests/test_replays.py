"""Every saved moment in tests/fixtures/ replays and passes every check."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.core import checks
from tests.replay import fixtures, replay, replay_calibration


@pytest.mark.parametrize("path", fixtures(), ids=lambda p: p.stem)
def test_a_saved_moment_passes_every_check(path: Path) -> None:
    inputs, result = replay(path)

    checks.check_all(path.stem, inputs, result)


@pytest.mark.parametrize("path", fixtures(), ids=lambda p: p.stem)
def test_a_saved_moments_backtest_replays(path: Path) -> None:
    cal = replay_calibration(path)

    assert cal.standby_kw >= 0
    for error in cal.honest_error_kwh.values():
        assert error is None or error >= 0
    # Only evenings that could have gone either way are scored.
    assert set(cal.score.contested) <= set(cal.score.nights)
