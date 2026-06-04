"""Typed data models for the TECO integration."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date


def to_jsonable(obj):
    """Recursively convert dataclasses/dates into JSON-serializable types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


@dataclass
class MonthlyUsage:
    """One month of usage.

    Authoritative values (total_kwh, cost, days) come from the InteractiveBill
    `meterDataMonthlyUsage` API. `daily_avg_kwh` is the dashboard-chart fallback.
    """

    month: str                            # label e.g. "May 2025" or "Jun-2025"
    daily_avg_kwh: float | None = None    # dashboard "Daily Avg KWH" chart
    total_kwh: float | None = None        # actual monthly kWh (ibill)
    cost: float | None = None             # actual monthly cost USD (ibill)
    days: int | None = None
    temperature: float | None = None      # avg outdoor temp (F)


@dataclass
class DailyUsage:
    """One day of metered usage from the InteractiveBill `meterDataDailyUsage` API."""

    date: date | None
    kwh: float | None
    temperature: float | None = None      # avg outdoor temp (F)
    estimated: bool = False               # status != "A" (actual)


@dataclass
class BillRecord:
    """A bill: summary row (from Bill History) enriched with service-period detail.

    bill_date/amount/due_date come from the Bill History table. The
    service_period_* and kwh_used fields come from the InteractiveBill detail and
    let Home Assistant align its measured usage to the exact billed window. `cost`
    is the period's electric cost (== amount unless gas is bundled and split out).
    """

    bill_date: date | None
    amount: float | None                 # total billed amount (USD)
    due_date: date | None
    view_url: str | None = None
    # --- service-period detail (from InteractiveBill MeterData/ChargeDetails) ---
    service_period_start: date | None = None
    service_period_end: date | None = None
    service_days: int | None = None
    kwh_used: float | None = None        # metered kWh for the service period
    cost: float | None = None            # electric cost for the service period (USD)
    cost_per_kwh: float | None = None    # absolute $/kWh = cost / kwh_used (this bill)
    invoice_id: str | None = None        # 'inid' from the ViewBill link / BillSelector
    meter_number: str | None = None
    previous_reading: float | None = None
    current_reading: float | None = None


@dataclass
class PaymentRecord:
    """One row from the Payment History table."""

    amount: float | None       # amount paid (USD)
    date: date | None


@dataclass
class CurrentBill:
    """The Current Bill panel."""

    bill_date: date | None = None
    current_charges: float | None = None
    total_amount_due: float | None = None
    due_date: date | None = None
    view_bill_url: str | None = None


@dataclass
class AccountInfo:
    """Account identifiers and type from hidden inputs / page."""

    account_id: str | None = None          # billing account
    contract_account_id: str | None = None
    account_type: str | None = None        # e.g. "Standard"
    status: str | None = None              # "Active" etc. (if present)
    interactive_billing: bool = False


@dataclass
class TecoData:
    """Everything one refresh produces — what the coordinator hands to entities."""

    account: AccountInfo = field(default_factory=AccountInfo)
    current_bill: CurrentBill = field(default_factory=CurrentBill)
    monthly_usage: list[MonthlyUsage] = field(default_factory=list)
    bills: list[BillRecord] = field(default_factory=list)
    payments: list[PaymentRecord] = field(default_factory=list)
    # program / setting flags from /Dashboard/Get*Status (filled by client, not parser)
    flags: dict[str, bool | None] = field(default_factory=dict)
