"""Draw each card for the docs, in headless Chrome, from the frontend tests'
recorded data: python scripts/screenshots.py

Needs Playwright (pip install playwright, then a Chrome or `playwright install
chromium`) and the Material Design Icons as SVG files, from `npm i @mdi/svg`
(node_modules/@mdi/svg/svg) or any folder of mdi SVGs given as MDI_SVG_DIR.
Set CHROME to use an installed Chrome. Writes docs/images/<card>.png.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    ROOT / "custom_components/solar_energy_insights/frontend/solar-energy-insights.js"
)
FIXTURES = ROOT / "tests/frontend"
OUT = ROOT / "docs/images"
WIDTH = 380  # a phone's column

APPLIANCES = [
    {"name": "Dishwasher", "icon": "mdi:dishwasher", "kwh": 1.0, "hours": 2.5},
    {"name": "Dryer", "icon": "mdi:tumble-dryer", "kwh": 1.5, "hours": 2.0},
]
# Each card as a user adds it, with the settings worth showing.
CARDS = [
    ("now", {}),
    ("until-sunrise", {"appliances": APPLIANCES}),
    ("best-time", {"appliances": APPLIANCES[:1]}),
    ("appliances", {"appliances": APPLIANCES, "view": "today"}),
    ("forecast-trust", {}),
    (
        "scenario-lab",
        {
            "extra_battery_kwh": 5,
            "battery_price_per_kwh": 400,
            "heat_loss_w_per_k": 150,
            "heating_limit_c": 12.5,
            "gas_price": 0.11,
        },
    ),
    ("moments", {}),
    ("level", {}),
    ("streaks", {}),
    ("records", {}),
    ("badges", {}),
]


def mdi_dir() -> Path:
    for candidate in (
        os.environ.get("MDI_SVG_DIR"),
        ROOT / "node_modules/@mdi/svg/svg",
    ):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    sys.exit("No Material Design Icons: npm i @mdi/svg, or set MDI_SVG_DIR")


def icons(text: str) -> dict[str, str]:
    """The SVG path of every mdi icon the cards or the data may name."""
    folder = mdi_dir()
    out = {}
    for name in sorted(set(re.findall(r"mdi:([a-z0-9-]+)", text))):
        svg = folder / f"{name}.svg"
        if svg.is_file():
            match = re.search(r' d="([^"]+)"', svg.read_text())
            if match:
                out[name] = match.group(1)
    return out


PAGE = Path(__file__).with_name("screenshots.html")


def main() -> None:
    solar = json.loads((FIXTURES / "solar.json").read_text())
    ach = json.loads((FIXTURES / "achievements.json").read_text())
    now = solar["now"]
    # Something just achieved, so the moments card has something to show.
    ach["moments"] = [
        {
            "kind": "level",
            "title": "Level 17: Corona",
            "explain": "Enough XP from days, quests and badges for the next level.",
            "icon": "mdi:star-four-points",
            "at": now,
        },
        {
            "kind": "badge",
            "tier": "silver",
            "title": "Night owl, silver",
            "explain": "A grid-free night after one of your dullest days.",
            "icon": "mdi:owl",
            "at": now,
        },
    ]
    script = SCRIPT.read_text()
    fill = {
        "WIDTH": str(WIDTH),
        "SOLAR": json.dumps(solar),
        "ACH": json.dumps(ach),
        "ICONS": json.dumps(
            icons(script + json.dumps(solar) + json.dumps(ach) + json.dumps(CARDS))
        ),
        "SCRIPT": script,
    }
    html = PAGE.read_text()
    for key, value in fill.items():
        html = html.replace("{{" + key + "}}", value)
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None)
        page = browser.new_page(
            viewport={"width": WIDTH, "height": 900}, device_scale_factor=2
        )
        page.set_content(html)
        for key, config in CARDS:
            page.evaluate("([k, c]) => window.draw(k, c)", [key, config])
            card = page.locator("#stage > *")
            card.screenshot(path=str(OUT / f"{key}.png"), omit_background=True)
            print("drew", key)
        browser.close()


if __name__ == "__main__":
    main()
