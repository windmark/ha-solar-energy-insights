"""Real moments from a real installation, and what each one taught.

2026-10-31, 10:18: a sunny morning after a fortnight of short autumn days.
The battery was at 55 %, expected to be full by 11:34 and at 95 % by sunset.
"""

from __future__ import annotations

from dataclasses import replace

from custom_components.solar_energy_insights.core.backtest import calibrate
from custom_components.solar_energy_insights.core.plan import Outlook, plan
from custom_components.solar_energy_insights.core.snapshot import (
    evenings_from_snapshot,
    from_snapshot,
)
from tests.core import checks
from tests.replay import FIXTURES, load, replay, replay_calibration

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


WEAK_AFTERNOON = FIXTURES / "2026-10-31-weak-afternoon.json"


def test_that_afternoon_was_rightly_borderline() -> None:
    """2026-10-31, 13:55: the battery would only reach 89 %, 83 % by sunset,
    after two weeks of nights using 5 to 10 kWh. Five of them would not have
    lasted from 83 %, including the night before."""
    _, result = replay(WEAK_AFTERNOON)

    assert result.night is not None
    assert result.night.outlook is Outlook.BORDERLINE
    assert not result.night.heavy.lasts
    assert result.night.lasting == 9


def test_recent_evenings_keep_the_hidden_load_learned() -> None:
    """Scoring 60 evenings reached back into September, whose noisy nights hid
    October's steady optimism: the hidden load was dropped and the plan was
    0.4 kWh optimistic again. A month of evenings follows the season."""
    data = load(WEAK_AFTERNOON)
    inputs = from_snapshot(data)
    everything = evenings_from_snapshot(data)

    recent = replay_calibration(WEAK_AFTERNOON)
    two_months = calibrate(
        inputs.use, inputs.tz, everything, inputs.battery, inputs.nights
    )

    assert len(everything) == 60
    assert two_months.standby_kw == 0
    assert 0.03 <= recent.standby_kw <= 0.06
    assert recent.error_kwh is not None
    assert two_months.error_kwh is not None
    assert recent.error_kwh < two_months.error_kwh * 0.75
    assert recent.score.bias_kwh is not None
    assert abs(recent.score.bias_kwh) < 0.25


def test_with_what_recent_evenings_teach_the_night_is_tighter_still() -> None:
    inputs, as_shown = replay(WEAK_AFTERNOON)
    cal = replay_calibration(WEAK_AFTERNOON)

    learned = plan(
        replace(inputs, standby_kw=cal.standby_kw, heavy_share=cal.heavy_share)
    )

    assert as_shown.night is not None
    assert learned.night is not None
    assert learned.night.outlook is Outlook.BORDERLINE
    assert learned.night.typical.spare_kwh < as_shown.night.typical.spare_kwh
