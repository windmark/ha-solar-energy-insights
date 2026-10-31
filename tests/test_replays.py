"""Every saved moment in tests/fixtures/ replays and passes every check."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.core import checks
from tests.replay import fixtures, replay, replay_scores


@pytest.mark.parametrize("path", fixtures(), ids=lambda p: p.stem)
def test_a_saved_moment_passes_every_check(path: Path) -> None:
    inputs, result = replay(path)

    checks.check_all(path.stem, inputs, result)


@pytest.mark.parametrize("path", fixtures(), ids=lambda p: p.stem)
def test_a_saved_moments_backtest_replays(path: Path) -> None:
    _, scores = replay_scores(path)

    for result in scores.values():
        error = result.error_kwh
        assert error is None or error >= 0
