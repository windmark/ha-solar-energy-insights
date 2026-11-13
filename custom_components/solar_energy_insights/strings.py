"""Per-string sensors from PowerOcean's string power and the string sizes.

Energy comes from the recorder's hourly mean power (kW over an hour is kWh), so
it agrees with history; the energy sensor's statistics are backfilled once.

The recorder carries an energy sensor's running total on from its own
five-minute statistics, which a backfill doesn't write, so after the backfill it
starts the total again at zero. `repair_energy_sums` lifts it back into line,
or every month and year across that point would show nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    StatisticMeanType,
    async_import_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import PowerConverter

if TYPE_CHECKING:
    from datetime import date

KW = {"power": UnitOfPower.KILO_WATT}


def power_kw(state: State | None) -> float | None:
    """A power sensor's state in kW, while it reports one."""
    if state is None:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get("unit_of_measurement") or UnitOfPower.WATT
    try:
        return float(PowerConverter.convert(value, unit, UnitOfPower.KILO_WATT))
    except Exception:  # an odd unit is a missing reading
        return None


async def _means(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime, period: str
) -> list[Any]:
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, {entity_id}, period, KW, {"mean"}
    )
    return list(rows.get(entity_id) or [])


async def energy_today(hass: HomeAssistant, power: str, now: datetime) -> float:
    """kWh the string has made today: whole hours from the hourly mean power,
    the hour in progress from the five-minute means."""
    midnight = dt_util.start_of_local_day(now)
    hour = now.replace(minute=0, second=0, microsecond=0)
    whole = await _means(hass, power, midnight, hour, "hour")
    part = await _means(hass, power, hour, now, "5minute")
    kwh = sum(max(r.get("mean") or 0.0, 0.0) for r in whole)
    kwh += sum(max(r.get("mean") or 0.0, 0.0) * 5 / 60 for r in part)
    return round(kwh, 3)


async def backfill_energy(
    hass: HomeAssistant, power: str, energy: str, name: str, start: date | None
) -> int:
    """Fill in the energy sensor's long-term statistics from the string's power
    history, once: hour by hour, the mean power as the hour's energy, summed.
    Returns the hours filled in."""
    if start is None:
        return 0
    recorder = get_instance(hass)
    last = await recorder.async_add_executor_job(
        get_last_statistics, hass, 1, energy, True, {"sum"}
    )
    if last.get(energy):
        return 0  # it has statistics already: nothing to fill
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    begin = dt_util.start_of_local_day(datetime.combine(start, datetime.min.time()))
    rows = await _means(hass, power, begin, now - timedelta(hours=1), "hour")
    total, out = 0.0, []
    day: date | None = None
    for r in rows:
        kwh = max(r.get("mean") or 0.0, 0.0)
        t = dt_util.utc_from_timestamp(r["start"])
        local_day = dt_util.as_local(t).date()
        if local_day != day:
            day, today = local_day, 0.0
        today += kwh
        total += kwh
        out.append(StatisticData(start=t, state=round(today, 3), sum=round(total, 3)))
    if not out:
        return 0
    async_import_statistics(
        hass,
        StatisticMetaData(
            has_mean=False,
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
            name=name,
            source="recorder",
            statistic_id=energy,
            unit_class="energy",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ),
        out,
    )
    return len(out)


async def repair_energy_sums(
    hass: HomeAssistant, energy: str, prev: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any] | None]:
    """Lift the energy sensor's running total back into line wherever it
    dropped: a running total never falls, so a drop is where the recorder started
    its own at zero after the backfill. The hour after the drop gets what the
    sensor rose by in it (all of it on a new day), and every later hour moves
    with it. Checks from the hour after `prev`, the last hour seen before, or from
    the start. Returns the drops repaired and the last hour seen."""
    recorder = get_instance(hass)
    if prev is None:
        months = await recorder.async_add_executor_job(
            statistics_during_period,
            hass,
            datetime(2000, 1, 1, tzinfo=dt_util.UTC),
            None,
            {energy},
            "month",
            None,
            {"sum"},
        )
        first = months.get(energy) or []
        if not first:
            return 0, None
        begin = dt_util.utc_from_timestamp(first[0]["start"])
    else:
        begin = dt_util.utc_from_timestamp(prev["start"])
    rows = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        begin,
        None,
        {energy},
        "hour",
        None,
        {"sum", "state"},
    )
    shift, repaired = 0.0, 0
    for r in rows.get(energy) or []:
        if r.get("sum") is None or (prev is not None and r["start"] <= prev["start"]):
            continue
        here = float(r["sum"]) + shift
        if prev is not None and here < float(prev["sum"]) - 0.001:
            t = dt_util.utc_from_timestamp(r["start"])
            same_day = (
                dt_util.as_local(t).date()
                == dt_util.as_local(dt_util.utc_from_timestamp(prev["start"])).date()
            )
            state, before = (
                float(r.get("state") or 0.0),
                float(prev.get("state") or 0.0),
            )
            rose = max(state - before, 0.0) if same_day else max(state, 0.0)
            fix = float(prev["sum"]) + rose - here
            recorder.async_adjust_statistics(
                energy, t, fix, UnitOfEnergy.KILO_WATT_HOUR
            )
            shift += fix
            here += fix
            repaired += 1
        prev = {"start": r["start"], "sum": here, "state": r.get("state")}
    return repaired, prev
