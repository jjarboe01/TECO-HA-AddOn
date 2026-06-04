"""Feed TECO usage/cost into Home Assistant long-term statistics (Energy Dashboard).

Primary feed is DAILY kWh (the finest resolution TECO exposes). Cost is provided
as a parallel daily statistic, distributed from each bill's total cost across its
days in proportion to daily kWh — so the Energy Dashboard can show $/period that
reconciles to the actual bill.
"""
from __future__ import annotations

from datetime import datetime, date

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STAT_COST, STAT_ENERGY


def _as_date(s) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _midnight(d: date) -> datetime:
    """Local midnight as a tz-aware datetime (HA statistics period start)."""
    return dt_util.start_of_local_day(datetime(d.year, d.month, d.day))


def _rate_for(d: date, bills: list[dict]) -> float | None:
    for b in bills:
        s, e = _as_date(b.get("service_period_start")), _as_date(b.get("service_period_end"))
        if s and e and s <= d <= e and b.get("cost") and b.get("kwh_used"):
            return b["cost"] / b["kwh_used"]
    return None


async def async_import_statistics(hass: HomeAssistant, data: dict) -> None:
    daily = data.get("daily_usage") or []
    bills = data.get("bills") or []

    # sort by date, build cumulative sums
    points: list[tuple[date, float]] = []
    for d in daily:
        dt = _as_date(d.get("date"))
        kwh = d.get("kwh")
        if dt is not None and kwh is not None:
            points.append((dt, float(kwh)))
    points.sort(key=lambda x: x[0])
    if not points:
        return

    energy_stats: list[StatisticData] = []
    cost_stats: list[StatisticData] = []
    esum = 0.0
    csum = 0.0
    for dt, kwh in points:
        esum += kwh
        energy_stats.append(StatisticData(start=_midnight(dt), state=kwh, sum=esum))
        rate = _rate_for(dt, bills)
        if rate is not None:
            day_cost = round(kwh * rate, 4)
            csum += day_cost
            cost_stats.append(StatisticData(start=_midnight(dt), state=day_cost, sum=csum))

    energy_meta = StatisticMetaData(
        has_mean=False, has_sum=True, name="TECO Energy",
        source=DOMAIN, statistic_id=STAT_ENERGY, unit_of_measurement="kWh",
    )
    async_add_external_statistics(hass, energy_meta, energy_stats)

    if cost_stats:
        cost_meta = StatisticMetaData(
            has_mean=False, has_sum=True, name="TECO Energy Cost",
            source=DOMAIN, statistic_id=STAT_COST, unit_of_measurement="USD",
        )
        async_add_external_statistics(hass, cost_meta, cost_stats)
