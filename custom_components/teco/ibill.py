"""
Parsers for the InteractiveBill ("ibill") JSON APIs.

Source: https://miportal.tecoenergy.com/api/ibill/webcomponents/v1/Post/<Component>
(called inside the authenticated browser context by the sidecar). These return
clean, structured data — far better than scraping. Verified against captured
fixtures (tests/test_ibill.py).

Components used:
  MeterData            -> service period (DAP_StartDate/EndDate), reads, total kWh, $
  ChargeDetails        -> "Service Period: mm/dd/yyyy - mm/dd/yyyy" + line items
  BillSelector         -> list of every bill {label, invoice_id}
  meterDataMonthlyUsage-> per-month actual kWh + cost + days + temp
  meterDataDailyUsage  -> per-day actual kWh + temp
"""
from __future__ import annotations

from datetime import date, datetime

try:
    from .models import BillRecord, DailyUsage, MonthlyUsage
except ImportError:  # standalone (tests)
    from models import BillRecord, DailyUsage, MonthlyUsage  # type: ignore


def _num(text) -> float | None:
    """'4,121 kWh' / '749.31' / '3,832.0' -> float."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text)
    cleaned = "".join(c for c in s if c.isdigit() or c in ".-")
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _int(text) -> int | None:
    """'29 Days' / '30' -> int."""
    n = _num(text)
    return int(n) if n is not None else None


def _date_yyyymmdd(s: str | None) -> date | None:
    if not s or len(str(s)) != 8 or not str(s).isdigit():
        return None
    try:
        return datetime.strptime(str(s), "%Y%m%d").date()
    except ValueError:
        return None


def _date_mdy(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _date_text(s: str | None) -> date | None:
    """'April 17 2026' / 'May 2025' -> date (1st if no day)."""
    if not s:
        return None
    for fmt in ("%B %d %Y", "%b %d %Y", "%B %Y", "%b %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# MeterData -> service period / reads / total kWh / cost (for the selected bill)
# --------------------------------------------------------------------------- #
def parse_meter_data(payload: dict, invoice_id: str | None = None) -> BillRecord | None:
    table = (payload or {}).get("MeterTabel") or []
    electric = next((m for m in table if str(m.get("Service", "")).lower() == "electric"),
                    table[0] if table else None)
    if not electric:
        return None
    kwh = _num(electric.get("TotalUsed"))
    cost = _num(electric.get("BilledAmount"))
    cpk = round(cost / kwh, 5) if (cost is not None and kwh) else None
    return BillRecord(
        bill_date=None,
        amount=cost,
        due_date=None,
        invoice_id=invoice_id,
        service_period_start=_date_yyyymmdd(electric.get("DAP_StartDate")),
        service_period_end=_date_yyyymmdd(electric.get("DAP_EndDate")),
        service_days=_int(electric.get("BillingPeriod")),
        kwh_used=kwh,
        cost=cost,
        cost_per_kwh=cpk,
        meter_number=str(electric.get("MeterNumber")) if electric.get("MeterNumber") else None,
        previous_reading=_num(electric.get("PreviousReading")),
        current_reading=_num(electric.get("CurrentReading")),
    )


# --------------------------------------------------------------------------- #
# ChargeDetails -> service-period dates (cross-check) + total
# --------------------------------------------------------------------------- #
def parse_charge_details_service_period(payload: dict) -> tuple[date | None, date | None]:
    """Walk the nested Section tree, pull the two dates after 'Service Period:'."""
    dates: list[date] = []
    seen_label = False

    def walk(node):
        nonlocal seen_label
        if isinstance(node, dict):
            label = (node.get("Lable") or node.get("Label") or "")
            val = node.get("Value") or ""
            if "service period" in str(label).lower() or "service period" in str(val).lower():
                seen_label = True
            if seen_label and len(dates) < 2:
                d = _date_mdy(str(val))
                if d:
                    dates.append(d)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    start = dates[0] if len(dates) > 0 else None
    end = dates[1] if len(dates) > 1 else None
    return start, end


# --------------------------------------------------------------------------- #
# BillSelector -> [{label, invoice_id}]
# --------------------------------------------------------------------------- #
def parse_bill_selector(payload: dict) -> list[dict]:
    out = []
    for b in (payload or {}).get("bills", []):
        d = _date_text((b.get("lable") or "").replace(",", ""))
        out.append({
            "label": b.get("lable") or b.get("label"),
            "bill_date": d.isoformat() if d else None,  # ISO string -> JSON-safe
            "invoice_id": str(b.get("value")) if b.get("value") is not None else None,
        })
    return out


# --------------------------------------------------------------------------- #
# meterDataMonthlyUsage -> MonthlyUsage[] (actual kWh + cost)
# --------------------------------------------------------------------------- #
def parse_monthly_usage(payload: dict) -> list[MonthlyUsage]:
    rows = (payload or {}).get("MonthlyUsage", {}).get("MonthlyDetails", []) or []
    out: list[MonthlyUsage] = []
    for r in rows:
        out.append(MonthlyUsage(
            month=r.get("FullDate") or r.get("Perioddate") or "",
            total_kwh=_num(r.get("Usage")),
            cost=_num(r.get("Cost")),
            days=_int(r.get("Days")),
            temperature=_num(r.get("Temperature")),
        ))
    return out


# --------------------------------------------------------------------------- #
# meterDataDailyUsage -> DailyUsage[] (actual daily kWh)
# --------------------------------------------------------------------------- #
def parse_daily_usage(payload: dict) -> list[DailyUsage]:
    rows = (payload or {}).get("DailyUsage", {}).get("DailyDetails", []) or []
    out: list[DailyUsage] = []
    for r in rows:
        out.append(DailyUsage(
            date=_date_text(r.get("FullDate")),
            kwh=_num(r.get("Usage")),
            temperature=_num(r.get("Temperature")),
            estimated=str(r.get("status", "A")).upper() != "A",
        ))
    return out
