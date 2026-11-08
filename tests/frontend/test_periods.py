"""The shipped script's week, month and year helpers, run in Node: nothing is
fetched before the settings arrive, periods add up their days, today uses the
live counters, and months before the first statistics are never asked for."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_periods_add_up_their_days() -> None:
    script = Path(__file__).parent / "periods.mjs"
    result = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
