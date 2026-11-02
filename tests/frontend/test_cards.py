"""The achievement cards shipped in the integration's script, run in Node."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_the_achievement_cards_draw_and_take_taps() -> None:
    script = Path(__file__).parent / "cards.mjs"
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
