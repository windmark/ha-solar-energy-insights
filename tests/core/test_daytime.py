"""In the morning, the plan replays the rest of today too; the night is still
only what happens from sunset on. Each test is a morning that once went wrong."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from custom_components.solar_energy_insights.core.battery import Battery, simulate
from custom_components.solar_energy_insights.core.nights import past_days
from custom_components.solar_energy_insights.core.plan import PlanInputs, plan
from tests.core.helpers import TZ, at, sunny

DAY = date(2026, 10, 31)
NEXT = DAY + timedelta(days=1)
SUNRISE, SUNSET = at(NEXT, 7, 0), at(DAY, 16, 50)


def morning(soc: float = 10.0, **kwargs: Any) -> PlanInputs:
    """08:20 on a sunny autumn day, the battery nearly empty after the night."""
    values: dict[str, Any] = {
        "now": at(DAY, 8, 20),
        "tz": TZ,
        "sunrise": SUNRISE,
        "sunset": SUNSET,
        "battery": Battery(capacity_kwh=15.18, soc_pct=soc, floor_pct=5.0),
        "pv": sunny(DAY, NEXT, peak_kw=9),
        "use": {},
    }
    values.update(kwargs)
    return PlanInputs(**values)


def days(kwh_at: Any, count: int = 14) -> dict[tuple[date, int], float]:
    """Past days; kwh_at(days_ago, hour) gives each hour's use."""
    return {
        (DAY - timedelta(days=n), hour): kwh_at(n, hour)
        for n in range(1, count + 1)
        for hour in range(24)
    }


def evening_of(n: int, hour: int) -> int:
    """Which day's evening an hour belongs to: the small hours are the night
    that began the evening before."""
    return n if hour >= 7 else n + 1


def busy_day_quiet_night(n: int, hour: int) -> float:
    """Half the days are busy while the sun is up and quiet in the night after;
    the other half the reverse."""
    if evening_of(n, hour) % 2:
        return 2.0 if 9 <= hour < 16 else 0.3
    return 0.3 if 9 <= hour < 16 else 0.9


def test_a_heavy_night_never_ends_better_than_a_typical_one() -> None:
    result = plan(morning(use=days(busy_day_quiet_night)))

    night = result.night
    assert night is not None
    assert night.heavy.spare_kwh <= night.typical.spare_kwh
    assert night.heavy.grid_kwh >= night.typical.grid_kwh


def breakfast_then_quiet(_n: int, hour: int) -> float:
    """A big breakfast the morning sun cannot cover yet, then a light night."""
    return 6.0 if hour in (8, 9) else 0.3


def test_this_mornings_low_battery_is_not_a_failed_night() -> None:
    result = plan(morning(soc=8.0, use=days(breakfast_then_quiet)))

    night = result.night
    assert night is not None
    assert night.share == 1
    assert all(o.reserve_at is None for o in night.outcomes)


def test_reserve_and_grid_tonight_are_counted_from_sunset() -> None:
    # The reserve is reached at breakfast and again in the night; only the
    # night's counts.
    heavy_night = days(
        lambda _n, hour: (
            6.0 if hour in (8, 9) else 1.8 if hour >= 17 or hour < 7 else 0.3
        )
    )

    result = plan(morning(soc=8.0, use=heavy_night))

    night = result.night
    assert night is not None
    assert not night.typical.lasts
    reserve_at = night.typical.reserve_at
    assert reserve_at is not None
    assert SUNSET <= reserve_at < SUNRISE


def test_charge_needed_in_the_day_is_for_sunset_and_enough_from_there() -> None:
    use = days(lambda _n, hour: 1.2 if hour >= 17 or hour < 7 else 0.3)

    result = plan(morning(use=use))

    needed = result.needed
    assert needed is not None
    assert needed.at == SUNSET
    assert needed.heavy_pct is not None
    # It is about the night, so not the floor just because the sun will come.
    assert needed.heavy_pct > 5.0
    # And starting the night at that level gets every replayed night through.
    battery = Battery(capacity_kwh=15.18, soc_pct=needed.heavy_pct, floor_pct=5.0)
    pv = sunny(DAY, NEXT, peak_kw=9)
    for day in past_days(use, TZ, 14):
        assert simulate(battery, SUNSET, SUNRISE, pv.kw_at, day.rate).lasts


def test_charge_needed_at_night_is_for_now() -> None:
    use = days(lambda _n, hour: 1.0 if hour >= 17 or hour < 7 else 0.3)
    evening = morning(now=at(DAY, 21), sunset=at(NEXT, 16, 50), use=use)

    needed = plan(evening).needed

    assert needed is not None
    assert needed.at == at(DAY, 21)
