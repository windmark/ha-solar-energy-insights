"""The solar cards shipped in the integration's script, run in Node."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_the_solar_cards_draw_with_and_without_appliances() -> None:
    script = Path(__file__).parent / "solar_cards.mjs"
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
