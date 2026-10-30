"""Replaying the hours with a bigger battery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from custom_components.solar_energy_insights.core.upgrade import (
    Hour,
    ceiling_per_year,
    evaluate,
)
from tests.core.helpers import TZ

START = datetime(2026, 5, 1, tzinfo=UTC)


def days(count: int, export_by_day: float, import_by_night: float) -> list[Hour]:
    hours = []
    for h in range(count * 24):
        t = START + timedelta(hours=h)
        local = t.astimezone(TZ).hour
        hours.append(
            Hour(
                t,
                imp=import_by_night / 8 if local < 6 or local >= 22 else 0.0,
                exp=export_by_day / 6 if 10 <= local < 16 else 0.0,
            )
        )
    return hours


PRICES: dict[str, Any] = {
    "price_eur_kwh": 0.32,
    "feed_in_eur_kwh": 0.08,
    "round_trip": 0.9,
    "tz": TZ,
}


def value(hours, kwh):
    return evaluate(hours, kwh, max_kw=5.0, **PRICES)


def test_more_capacity_is_never_worth_less() -> None:
    hours = days(60, export_by_day=12, import_by_night=6)

    values = [value(hours, kwh).per_year_eur for kwh in (0, 2, 5, 10, 20)]

    # Charge kept but never needed costs only its round-trip loss.
    assert all(later >= earlier - 1 for earlier, later in pairwise(values))
    assert values[0] == 0
    assert values[2] > values[1]


def test_never_worth_more_than_the_ceiling() -> None:
    hours = days(60, export_by_day=12, import_by_night=6)
    ceiling = ceiling_per_year(hours, **PRICES)

    assert value(hours, 50).per_year_eur <= ceiling + 0.01


def test_nothing_to_store_is_worth_nothing() -> None:
    hours = days(40, export_by_day=0, import_by_night=6)

    assert value(hours, 10).per_year_eur == 0


def test_payback_follows_the_yearly_value() -> None:
    hours = days(60, export_by_day=12, import_by_night=6)
    result = value(hours, 8)

    payback = result.payback_years(1800)
    assert payback is not None
    assert payback > 0
    assert result.months_measured >= 1
