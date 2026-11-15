"""Loading the history: the recorder is asked for two months at a time, the
newest first, and months that are over are kept in the browser."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_history_loads_a_few_months_at_a_time_and_keeps_them() -> None:
    script = Path(__file__).parent / "loading.mjs"
    result = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
