"""The shipped script's requests, run in Node: a lost connection keeps the last
answer, says what happened, and retries soon and when the connection is back."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_requests_survive_a_lost_connection() -> None:
    script = Path(__file__).parent / "requests.mjs"
    result = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
