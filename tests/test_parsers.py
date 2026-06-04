"""
Verify the TECO parsers against captured fixtures.

Run from the project root:
    python3 tests/test_parsers.py        # plain run, prints a report
    pytest tests/test_parsers.py         # if pytest installed

Fixtures (fixtures/*.html) are gitignored (contain PII). This test asserts on
structure/shape, not on specific personal values.
"""
from __future__ import annotations

import os
import sys

# import the parser module directly (no HA package context needed)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sidecar"))

import parsers  # noqa: E402

FIX = os.path.join(ROOT, "fixtures")


def _load(name: str) -> str:
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def main() -> int:
    dash = _load("dashboard.html")

    usage = parsers.parse_monthly_usage(dash)
    bill = parsers.parse_current_bill(dash)
    bills = parsers.parse_bill_history(dash)
    payments = parsers.parse_payment_history(dash)
    acct = parsers.parse_account_info(dash)

    print("=== parse_monthly_usage ===")
    print(f"  months parsed: {len(usage)}")
    for u in usage[:3] + usage[-2:]:
        print(f"   {u.month}: {u.daily_avg_kwh} kWh/day")

    print("\n=== parse_current_bill ===")
    print(f"  bill_date        : {bill.bill_date}")
    print(f"  current_charges  : {bill.current_charges}")
    print(f"  total_amount_due : {bill.total_amount_due}")
    print(f"  due_date         : {bill.due_date}")
    print(f"  view_bill_url    : {'present' if bill.view_bill_url else 'none'}")

    print("\n=== parse_bill_history ===")
    print(f"  rows: {len(bills)}")
    for b in bills[:3]:
        print(f"   {b.bill_date} | ${b.amount} | due {b.due_date}")

    print("\n=== parse_payment_history ===")
    print(f"  rows: {len(payments)}")
    for p in payments[:3]:
        print(f"   {p.date} | ${p.amount}")

    print("\n=== parse_account_info ===")
    print(f"  account_id present     : {bool(acct.account_id)}")
    print(f"  contract_account_id    : {bool(acct.contract_account_id)}")
    print(f"  account_type           : {acct.account_type}")
    print(f"  interactive_billing    : {acct.interactive_billing}")

    # --- assertions (shape, not values) ---
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + name)
        ok = ok and cond

    print("\n=== CHECKS ===")
    check("usage has >=12 months", len(usage) >= 12)
    check("usage values are floats > 0", all(isinstance(u.daily_avg_kwh, float) for u in usage) and any(u.daily_avg_kwh > 0 for u in usage))
    check("current bill total_amount_due parsed", bill.total_amount_due is not None)
    check("current bill due_date parsed", bill.due_date is not None)
    check("bill history has rows", len(bills) >= 1)
    check("bill rows have date+amount", all(b.bill_date and b.amount is not None for b in bills))
    check("account_type parsed", acct.account_type is not None)

    print("\nRESULT:", "ALL PASS ✅" if ok else "FAILURES ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
