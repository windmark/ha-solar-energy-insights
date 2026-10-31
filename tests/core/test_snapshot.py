"""A saved moment replays exactly: same inputs, same answers."""

from __future__ import annotations

import json

import pytest

from custom_components.solar_energy_insights.core.backtest import (
    Evening,
    score,
)
from custom_components.solar_energy_insights.core.plan import PlanInputs, plan
from custom_components.solar_energy_insights.core.snapshot import (
    evenings_from_snapshot,
    from_snapshot,
    to_snapshot,
)
from tests.core.helpers import at
from tests.core.scenarios import HOUSES, SEASONS, SKIES, history, inputs, moments

SUMMER, WINTER = SEASONS


def sample() -> list:
    """A spread of the grid."""
    out: list[PlanInputs] = []
    for season in SEASONS:
        for house, peak in zip(HOUSES.values(), SKIES.values(), strict=True):
            use = history(season.day, house)
            out.extend(
                inputs(season, now, peak_kw=peak, use=use, soc=35)
                for now in moments(season)[::3]
            )
    return out


def through_json(data: dict) -> dict:
    return json.loads(json.dumps(data))


@pytest.mark.parametrize("original", sample(), ids=lambda i: f"{i.now:%m-%d %H:%M}")
def test_a_replayed_moment_gives_the_same_answers(original) -> None:
    restored = from_snapshot(through_json(to_snapshot(original)))

    assert plan(restored) == plan(original)


def test_the_scored_evenings_replay_too() -> None:
    use = history(WINTER.day, HOUSES["erratic"])
    original = inputs(WINTER, at(WINTER.day, 21), peak_kw=0, use=use, soc=50)
    evenings = [
        Evening(
            WINTER.day.replace(day=d), 70.0, at(WINTER.day.replace(day=d + 1), 8), 20.0
        )
        for d in (5, 6, 7, 8)
    ]

    data = through_json(to_snapshot(original, evenings))
    restored = from_snapshot(data)

    assert evenings_from_snapshot(data) == evenings
    assert score(restored.use, restored.tz, evenings, restored.battery, 7) == score(
        use, original.tz, evenings, original.battery, 7
    )


def test_a_whole_diagnostics_download_is_accepted() -> None:
    original = inputs(SUMMER, at(SUMMER.day, 9), peak_kw=8, use={}, soc=40)
    download = {
        "home_assistant": {"version": "2026.10.3"},
        "data": {"options": {}, "replay": to_snapshot(original)},
    }

    assert from_snapshot(through_json(download)) == original


def test_an_unknown_format_is_refused_rather_than_misread() -> None:
    data = to_snapshot(inputs(SUMMER, at(SUMMER.day, 9), peak_kw=8, use={}, soc=40))
    data["version"] = 99

    with pytest.raises(ValueError, match="version"):
        from_snapshot(data)
