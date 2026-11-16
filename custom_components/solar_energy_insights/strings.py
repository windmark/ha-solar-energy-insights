"""Per-string sensors from each string's power and the string sizes.

A string's energy is its share of the solar counter: each interval's counted
energy, shared between the strings by their mean power in it (kW over an hour
is kWh). So the strings add up to the counter. Without a solar counter, or for
an interval it has no reading for, the energy from power stands. The energy
sensor's statistics are built this way once, and again when the counter changes.

The recorder carries an energy sensor's running total on from its own
five-minute statistics, which a backfill doesn't write, so after the backfill it
starts the total again at zero. `repair_energy_sums` lifts it back into line,
or every month and year across that point would show nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
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

from .core.nights import plausible
from .core.split import split

if TYPE_CHECKING:
    from datetime import date

KW = {"power": UnitOfPower.KILO_WATT, "energy": UnitOfEnergy.KILO_WATT_HOUR}
SLOT_HOURS = {"hour": 1.0, "5minute": 5 / 60}


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


async def string_energy(
    hass: HomeAssistant,
    powers: Mapping[int, str],
    solar: str | None,
    start: datetime,
    end: datetime,
    period: str,
) -> dict[int, dict[datetime, float]]:
    """Each string's kWh per interval from start to end: its share of the solar
    counter where there is one, else its energy from power."""
    ids = set(powers.values()) | ({solar} if solar else set())
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        ids,
        period,
        KW,
        {"mean", "change"},
    )
    hours = SLOT_HOURS[period]

    def at(row: Mapping[str, Any]) -> datetime:
        return dt_util.utc_from_timestamp(row["start"])

    energy = {
        n: {
            at(r): max(mean, 0.0) * hours
            for r in rows.get(entity_id) or []
            if (mean := r.get("mean")) is not None
        }
        for n, entity_id in powers.items()
    }
    made = (
        plausible(
            {
                at(r): change
                for r in rows.get(solar) or []
                if (change := r.get("change")) is not None
            }
        )
        if solar
        else {}
    )
    return split(made, energy)


async def energy_today(
    hass: HomeAssistant,
    n: int,
    powers: Mapping[int, str],
    solar: str | None,
    now: datetime,
) -> float:
    """kWh string n has made today: whole hours from the hourly statistics, the
    hour in progress from the five-minute ones."""
    # start_of_local_day takes a datetime's own date: a UTC one would give
    # yesterday's midnight in the first local hours, and yesterday's total.
    midnight = dt_util.start_of_local_day(dt_util.as_local(now))
    hour = now.replace(minute=0, second=0, microsecond=0)
    whole = await string_energy(hass, powers, solar, midnight, hour, "hour")
    part = await string_energy(hass, powers, solar, hour, now, "5minute")
    kwh = sum(whole.get(n, {}).values()) + sum(part.get(n, {}).values())
    return round(kwh, 3)


async def backfill_energy(
    hass: HomeAssistant,
    n: int,
    powers: Mapping[int, str],
    solar: str | None,
    energy: str,
    name: str,
    start: date | None,
    *,
    rebuild: bool = False,
) -> int:
    """Write the energy sensor's long-term statistics from the string's history,
    hour by hour, summed: once, or over what is there when rebuilding.
    Returns the hours written."""
    if start is None:
        return 0
    recorder = get_instance(hass)
    last = await recorder.async_add_executor_job(
        get_last_statistics, hass, 1, energy, True, {"sum"}
    )
    if last.get(energy) and not rebuild:
        return 0  # it has statistics already: nothing to fill
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    begin = dt_util.start_of_local_day(datetime.combine(start, datetime.min.time()))
    hours = (
        await string_energy(
            hass, powers, solar, begin, now - timedelta(hours=1), "hour"
        )
    ).get(n, {})
    total, out = 0.0, []
    day: date | None = None
    for t in sorted(hours):
        kwh = hours[t]
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
        total = r.get("sum")
        if total is None or (prev is not None and r["start"] <= prev["start"]):
            continue
        here = total + shift
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
