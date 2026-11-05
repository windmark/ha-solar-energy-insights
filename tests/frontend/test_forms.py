"""Every card's editor form is valid for Home Assistant's selectors."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from homeassistant.helpers.selector import selector


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_every_editor_form_is_valid_for_home_assistant() -> None:
    script = Path(__file__).parent / "forms.mjs"
    result = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    forms = json.loads(result.stdout)

    assert len(forms) == 6
    for tag, form in forms.items():
        names = [field["name"] for field in form["schema"]]
        for field in form["schema"]:
            selector(field["selector"])  # raises on anything Home Assistant rejects
        assert set(form["stub"]) <= set(names), tag
