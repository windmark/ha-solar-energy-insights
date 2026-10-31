"""Real moments from a real installation, and what each one taught.

2026-10-31, 10:18: a sunny morning after a fortnight of short autumn days.
The battery was at 55 %, expected to be full by 11:34 and at 95 % by sunset.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.solar_energy_insights.core.backtest import Weighting
from custom_components.solar_energy_insights.core.plan import Outlook, plan
from tests.core import checks
from tests.replay import FIXTURES, replay, replay_calibration

BORDERLINE = FIXTURES / "2026-10-31-borderline-morning.json"


def test_that_morning_was_borderline_not_a_plain_yes() -> None:
    """A typical night lasted with 2 kWh to spare; a heavy one ran out 25 minutes
    before sunrise. "Lasts the night: yes" hid that."""
    inputs, result = replay(BORDERLINE)

    assert result.night is not None
    assert result.night.outlook is Outlook.BORDERLINE
    checks.check_all(BORDERLINE.stem, inputs, result)


def test_only_evenings_that_could_have_gone_either_way_are_scored() -> None:
    """Half the fortnight's evenings started nearly empty after dull days and
    ended at the reserve whatever was predicted: easy wins that hid the error."""
    cal = replay_calibration(BORDERLINE)

    assert len(cal.score.nights) == 14
    assert len(cal.score.contested) == 7


def test_the_hidden_load_explains_why_predictions_were_optimistic() -> None:
    """The battery ended each contested night about 0.5 kWh lower than predicted:
    some 50 W the house counter never sees, such as the inverter itself."""
    cal = replay_calibration(BORDERLINE)

    assert cal.uncorrected.bias_kwh is not None
    assert cal.uncorrected.bias_kwh > 0.4
    assert 0.03 <= cal.standby_kw <= 0.07
    assert cal.score.bias_kwh is not None
    assert abs(cal.score.bias_kwh) < 0.1
    honest = cal.error_kwh
    assert honest is not None
    assert cal.uncorrected.error_kwh is not None
    assert honest < cal.uncorrected.error_kwh


@pytest.mark.parametrize("current", list(Weighting), ids=str)
def test_nights_like_tonight_win_clearly_whatever_was_in_use(
    current: Weighting,
) -> None:
    cal = replay_calibration(BORDERLINE, current)

    assert cal.weighting is Weighting.SIMILAR


def test_with_the_hidden_load_a_heavy_night_needs_more_charge() -> None:
    inputs, before = replay(BORDERLINE)
    cal = replay_calibration(BORDERLINE)

    after = plan(replace(inputs, standby_kw=cal.standby_kw))

    assert before.needed is not None
    assert after.needed is not None
    assert before.needed.heavy_pct is not None
    assert (
        after.needed.heavy_pct is None
        or after.needed.heavy_pct > before.needed.heavy_pct
    )


def test_with_the_hidden_load_a_heavy_night_is_short_even_from_full() -> None:
    """Two of that fortnight's nights ran out even before the hidden load was
    counted; with it, a heavy night needs the grid even from a full battery."""
    inputs, _ = replay(BORDERLINE)
    cal = replay_calibration(BORDERLINE)

    result = plan(replace(inputs, standby_kw=cal.standby_kw))

    assert result.needed is not None
    assert result.needed.heavy_pct is None
    assert result.needed.heavy_short_kwh > 0
