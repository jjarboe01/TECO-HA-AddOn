"""
Pure HTML/JSON parsers for the TECO portal dashboard.

These functions take raw HTML (as fetched by the aiohttp client using the
sidecar-provided session cookies) and return typed models. They run NO
JavaScript — all data is server-rendered into the dashboard markup:

  - monthly kWh   -> inline Highcharts JSON: categories[] + {"name":"KWH",
                     "yAxis":"ElectricAxes", ...,"data":[{"y":..}]}
  - current bill  -> label/value <div class="p-1"> pairs under #CurrentBillSection
  - bill history  -> #BillHistoryData table
  - payment hist. -> #PaymentHistoryData table
  - account ids   -> hidden inputs #accountId / #contractAccountId / #AccountType

Verified against captured fixtures (see tests/test_parsers.py).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

from bs4 import BeautifulSoup

try:  # package context (inside Home Assistant)
    from .models import (
        AccountInfo,
        BillRecord,
        CurrentBill,
        MonthlyUsage,
        PaymentRecord,
    )
except ImportError:  # standalone import (unit tests without HA)
    from models import (  # type: ignore[no-redef]
        AccountInfo,
        BillRecord,
        CurrentBill,
        MonthlyUsage,
        PaymentRecord,
    )

_MONEY_RE = re.compile(r"-?\$?\s*([\d,]+\.\d{2})")
_DATE_FMTS = ("%m/%d/%Y", "%m/%d/%y")


def _money(text: str | None) -> float | None:
    if not text:
        return None
    m = _MONEY_RE.search(text)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    return -val if text.strip().startswith("-") else val


def _parse_date(text: str | None) -> date | None:
    if not text:
        return None
    text = text.strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Monthly kWh (inline Highcharts JSON)
# --------------------------------------------------------------------------- #
_CATEGORIES_RE = re.compile(r'categories"\s*:\s*(\[[^\]]*\])')
# the electric KWH series: name + axis marker, then its data array (order-tolerant)
_KWH_SERIES_RE = re.compile(
    r'\{[^{}]*"name"\s*:\s*"KWH"[^{}]*"yAxis"\s*:\s*"ElectricAxes"[^{}]*\}'
    r'|\{[^{}]*"yAxis"\s*:\s*"ElectricAxes"[^{}]*"name"\s*:\s*"KWH"[^{}]*\}'
)
_DATA_ARRAY_RE = re.compile(r'"data"\s*:\s*(\[[^\]]*\])')
_Y_RE = re.compile(r'"y"\s*:\s*(-?\d+(?:\.\d+)?)')


def parse_monthly_usage(html: str) -> list[MonthlyUsage]:
    """Extract the electric KWH series (daily-avg kWh per billing month)."""
    cat_m = _CATEGORIES_RE.search(html)
    if not cat_m:
        return []
    try:
        categories = json.loads(cat_m.group(1))
    except json.JSONDecodeError:
        return []

    # Find the electric KWH series object and pull its data:[{"y":..}]
    values: list[float] = []
    ser = _KWH_SERIES_RE.search(html)
    if ser:
        data_m = _DATA_ARRAY_RE.search(ser.group(0))
        if data_m:
            values = [float(y) for y in _Y_RE.findall(data_m.group(1))]
    if not values:
        # fallback: first data:[{"y":..}] occurring after the categories block
        data_m = _DATA_ARRAY_RE.search(html, cat_m.end())
        if data_m:
            values = [float(y) for y in _Y_RE.findall(data_m.group(1))]

    out: list[MonthlyUsage] = []
    for i, month in enumerate(categories):
        val = values[i] if i < len(values) else None
        if val is None:
            continue
        out.append(MonthlyUsage(month=str(month), daily_avg_kwh=val))
    return out


# --------------------------------------------------------------------------- #
# Current bill (label/value .p-1 pairs)
# --------------------------------------------------------------------------- #
def _value_after_label(soup: BeautifulSoup, label_re: re.Pattern) -> str | None:
    """Find a .p-1 div whose text matches label, return the sibling .p-1 value."""
    for div in soup.find_all("div", class_="p-1"):
        if label_re.search(div.get_text(strip=True)):
            sib = div.find_next_sibling("div", class_="p-1")
            if sib:
                return sib.get_text(strip=True)
    return None


def parse_current_bill(html: str) -> CurrentBill:
    soup = BeautifulSoup(html, "html.parser")
    cb = CurrentBill()
    cb.bill_date = _parse_date(_value_after_label(soup, re.compile(r"Bill Date", re.I)))
    cb.current_charges = _money(
        _value_after_label(soup, re.compile(r"Current month'?s charges", re.I))
    )
    cb.total_amount_due = _money(
        _value_after_label(soup, re.compile(r"Total amount due", re.I))
    )
    cb.due_date = _parse_date(_value_after_label(soup, re.compile(r"Due Date", re.I)))
    link = soup.find("a", id="currentBillViewButton")
    if link and link.get("href"):
        cb.view_bill_url = link["href"]
    return cb


# --------------------------------------------------------------------------- #
# Bill history / payment history tables
# --------------------------------------------------------------------------- #
def _table_rows(soup: BeautifulSoup, container_id: str) -> list[list[str]]:
    container = soup.find(id=container_id)
    if not container:
        return []
    table = container.find("table") if container.name != "table" else container
    if not table:
        return []
    rows: list[list[str]] = []
    seen: set[tuple] = set()
    for tr in table.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cells:
            continue
        key = tuple(cells)
        if key in seen:  # the page renders duplicate desktop/mobile rows
            continue
        seen.add(key)
        # capture a view link if present
        a = tr.find("a", href=True)
        cells.append(a["href"] if a else "")
        rows.append(cells)
    return rows


def parse_bill_history(html: str) -> list[BillRecord]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[BillRecord] = []
    for cells in _table_rows(soup, "BillHistoryData"):
        # columns: Bill date | Amount | Due date | (view link appended)
        out.append(
            BillRecord(
                bill_date=_parse_date(cells[0]) if len(cells) > 0 else None,
                amount=_money(cells[1]) if len(cells) > 1 else None,
                due_date=_parse_date(cells[2]) if len(cells) > 2 else None,
                view_url=cells[-1] or None,
            )
        )
    return out


def parse_payment_history(html: str) -> list[PaymentRecord]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[PaymentRecord] = []
    for cells in _table_rows(soup, "PaymentHistoryData"):
        # columns: Amount paid | Date  (order verified against fixture)
        amt = next((c for c in cells if _money(c) is not None), None)
        dt = next((c for c in cells if _parse_date(c) is not None), None)
        out.append(PaymentRecord(amount=_money(amt), date=_parse_date(dt)))
    return out


# --------------------------------------------------------------------------- #
# Account info (hidden inputs)
# --------------------------------------------------------------------------- #
def _hidden(soup: BeautifulSoup, input_id: str) -> str | None:
    el = soup.find("input", id=input_id)
    if el and el.has_attr("value"):
        return el["value"].strip() or None
    return None


def parse_account_info(html: str) -> AccountInfo:
    soup = BeautifulSoup(html, "html.parser")
    info = AccountInfo()
    info.account_id = _hidden(soup, "accountId")
    info.contract_account_id = _hidden(soup, "contractAccountId")
    info.account_type = _hidden(soup, "AccountType")
    info.status = _hidden(soup, "AccountStatus")
    info.interactive_billing = (_hidden(soup, "interactiveBillingFeatureFlagEnabled") or "").lower() == "true"
    return info
