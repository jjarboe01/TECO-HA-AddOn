"""Verify the ibill JSON parsers against captured fixtures."""
from __future__ import annotations

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sidecar"))

import ibill  # noqa: E402

FIX = os.path.join(ROOT, "fixtures")


def _load(name: str) -> dict:
    """Fixture files start with two // comment lines, then JSON."""
    path = os.path.join(FIX, name)
    raw = open(path, encoding="utf-8").read()
    body = raw.split("\n", 2)[-1] if raw.startswith("//") else raw
    return json.loads(body)


def _first(pattern: str) -> str:
    matches = glob.glob(os.path.join(FIX, pattern))
    if not matches:
        raise FileNotFoundError(pattern)
    return os.path.basename(matches[0])


def main() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))
        ok = ok and cond

    # MeterData
    print("=== MeterData ===")
    bill = ibill.parse_meter_data(_load("ibill_MeterData.json"), invoice_id="TEST")
    print(f"  service: {bill.service_period_start} -> {bill.service_period_end} "
          f"({bill.service_days}d)  kwh={bill.kwh_used}  cost=${bill.cost}")
    print(f"  reads: {bill.previous_reading} -> {bill.current_reading}  meter={bill.meter_number}")
    check("service period start parsed", bill.service_period_start is not None)
    check("service period end parsed", bill.service_period_end is not None)
    check("kwh_used parsed", bill.kwh_used and bill.kwh_used > 0)
    check("cost parsed", bill.cost and bill.cost > 0)
    check("reads parsed", bill.previous_reading is not None and bill.current_reading is not None)

    # ChargeDetails service period (cross-check)
    print("\n=== ChargeDetails service period ===")
    s, e = ibill.parse_charge_details_service_period(_load("ibill_ChargeDetails.json"))
    print(f"  {s} -> {e}")
    check("charge-details period matches meterdata",
          s == bill.service_period_start and e == bill.service_period_end,
          f"{s}..{e} vs {bill.service_period_start}..{bill.service_period_end}")

    # BillSelector
    print("\n=== BillSelector ===")
    bills = ibill.parse_bill_selector(_load("ibill_BillSelector.json"))
    print(f"  bills: {len(bills)}; newest: {bills[0]['label']} -> inid {bills[0]['invoice_id']}")
    check("bill list parsed", len(bills) >= 12)
    check("bills have invoice ids + dates",
          all(b["invoice_id"] and b["bill_date"] for b in bills[:12]))

    # Monthly usage
    print("\n=== meterDataMonthlyUsage ===")
    monthly = ibill.parse_monthly_usage(_load(_first("ibill_meterDataMonthlyUsage*")))
    print(f"  months: {len(monthly)}; e.g. {monthly[0].month}: "
          f"{monthly[0].total_kwh} kWh / ${monthly[0].cost} / {monthly[0].days}d")
    check("monthly rows parsed", len(monthly) >= 12)
    check("monthly has kWh + cost",
          all(m.total_kwh is not None and m.cost is not None for m in monthly))

    # Daily usage
    print("\n=== meterDataDailyUsage ===")
    daily = ibill.parse_daily_usage(_load(_first("ibill_meterDataDailyUsage*")))
    print(f"  days: {len(daily)}; first {daily[0].date}={daily[0].kwh} kWh @ {daily[0].temperature}F; "
          f"last {daily[-1].date}={daily[-1].kwh} kWh")
    check("daily rows parsed", len(daily) >= 28)
    check("daily has date + kwh", all(d.date and d.kwh is not None for d in daily))
    check("daily sum ~ monthly bill kwh",
          abs(sum(d.kwh for d in daily) - (bill.kwh_used or 0)) / (bill.kwh_used or 1) < 0.10,
          f"daily_sum={sum(d.kwh for d in daily):.0f} vs bill={bill.kwh_used}")

    print("\nRESULT:", "ALL PASS ✅" if ok else "FAILURES ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
