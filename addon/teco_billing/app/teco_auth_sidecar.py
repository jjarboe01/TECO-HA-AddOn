"""
TECO auth + data sidecar.

Owns the *browser* half of the integration. Logs into account.tecoenergy.com with
headless Chromium (the only way past reCAPTCHA v3 + Cloudflare + NetScaler), then
drives the InteractiveBill ("ibill") JSON APIs to assemble rich, structured data:

  - per-bill service period + metered kWh + cost + reads   (MeterData / ChargeDetails)
  - actual DAILY kWh + temperature                          (meterDataDailyUsage)
  - actual MONTHLY kWh + cost                               (meterDataMonthlyUsage)
  - account info / current bill / program flags             (dashboard scrape + Get*Status)

It fetches each bill's detail by navigating to ViewBill?caid=..&inid=.. and
intercepting the components' network responses (robust — no auth/CORS replay).
Bills are cached on disk by invoice id, so the multi-year backfill happens once
and routine refreshes only fetch new bills.

Home Assistant is a thin client: GET /data -> the assembled JSON.

ENV:
  TECO_USER, TECO_PASS   (required)
  SIDECAR_TOKEN          (optional) require 'X-Auth-Token' on /data
  BACKFILL_BILLS         (default 36) how many bills to pull on first run
  SESSION_TTL_MIN        (default 30)
  CACHE_DIR              (default ./cache)
  HEADLESS               (default 1)

RUN:
  uvicorn teco_auth_sidecar:app --host 0.0.0.0 --port 8089
  # or validate once without the server:
  python teco_auth_sidecar.py --once [--force]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# verified parsers/models (vendored next to this file in the image)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "custom_components", "teco"))
import parsers   # noqa: E402
import ibill     # noqa: E402
import models    # noqa: E402

from playwright.async_api import async_playwright, Browser, BrowserContext, Page  # noqa: E402

LOG = logging.getLogger("teco.sidecar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE = "https://account.tecoenergy.com"
LOGIN_URL = BASE + "/"
VIEWBILL = BASE + "/InteractiveBill/ViewBill?caid={caid}&inid=ISU{inid}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

SESSION_TTL = int(os.environ.get("SESSION_TTL_MIN", "30")) * 60
BACKFILL_BILLS = int(os.environ.get("BACKFILL_BILLS", "36"))
HEADLESS = os.environ.get("HEADLESS", "1") != "0"
TOKEN = os.environ.get("SIDECAR_TOKEN")
CACHE_DIR = os.environ.get("CACHE_DIR", os.path.join(os.path.dirname(__file__), "cache"))
CACHE_FILE = os.path.join(CACHE_DIR, "bills.json")

STATUS_ENDPOINTS = {
    "paperless": "/Dashboard/GetPaperlessBillingStatus",
    "autopay": "/Dashboard/GetDirectDebitStatus",
    "budget_billing": "/Dashboard/GetBudgetBillingStatus",
    "sun_select": "/Dashboard/GetSunSelectStatus",
    "energy_planner": "/Dashboard/GetEnergyPlannerStatus",
    "prime_time_plus": "/Dashboard/GetPrimetimePlusStatus",
    "power_updates": "/Dashboard/GetPowerUpdatesStatus",
}
# ibill components we care about, by the URL's trailing path segment (lowercased)
WANT_COMPONENTS = {"meterdata", "chargedetails", "billselector", "meterdatadailyusage"}


def _coerce_flag(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "on", "yes", "enrolled", "active", "1"):
            return True
        if v in ("false", "off", "no", "not enrolled", "inactive", "0", ""):
            return False
        return None
    if isinstance(value, dict):
        for k in ("enrolled", "isEnrolled", "status", "result", "value", "data"):
            if k in value:
                return _coerce_flag(value[k])
    return None


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(models.to_jsonable(cache), f)  # coerce any stray date/dataclass
    os.replace(tmp, CACHE_FILE)


class TecoSession:
    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        self._logged_in_at = 0.0
        self._lock = asyncio.Lock()
        self._cache = _load_cache()  # invoice_id -> parsed bill dict

    # ---- browser / auth ---------------------------------------------------- #
    async def _ensure_browser(self) -> None:
        if self._browser:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._ctx = await self._browser.new_context(
            user_agent=UA, viewport={"width": 1440, "height": 900},
            locale="en-US", timezone_id="America/New_York",
        )
        self._page = await self._ctx.new_page()

    async def _is_logged_in(self) -> bool:
        try:
            body = (await self._page.inner_text("body")).lower()
            return "log off" in body or "logout" in body
        except Exception:
            return False

    async def _login(self) -> None:
        user, pw = os.environ.get("TECO_USER"), os.environ.get("TECO_PASS")
        if not user or not pw:
            raise RuntimeError("TECO_USER / TECO_PASS not set")
        await self._ensure_browser()
        LOG.info("logging in ...")
        await self._page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
        await self._page.fill("#UserName", user)
        await self._page.fill("#Credentials_Password", pw)
        try:
            async with self._page.expect_navigation(wait_until="networkidle", timeout=60000):
                await self._page.click("#login-submit")
        except Exception:
            pass
        await self._page.wait_for_timeout(2500)
        if not await self._is_logged_in():
            raise RuntimeError("login failed (reCAPTCHA score or bad credentials)")
        self._logged_in_at = time.time()
        LOG.info("login OK")

    async def _ensure_session(self) -> None:
        await self._ensure_browser()
        if (self._logged_in_at == 0
                or (time.time() - self._logged_in_at) > SESSION_TTL
                or not await self._is_logged_in()):
            await self._login()

    # ---- ibill collection -------------------------------------------------- #
    async def _navigate_collect(self, url: str) -> dict:
        """Navigate to `url` and collect the ibill component JSON responses."""
        collected: dict[str, dict] = {}

        async def on_response(resp):
            try:
                u = resp.url
                if "miportal.tecoenergy.com/api/ibill" not in u:
                    return
                comp = u.rstrip("/").split("/")[-1].split("?")[0].lower()
                if comp in WANT_COMPONENTS or "meterdatadaily" in comp:
                    body = await resp.text()
                    collected[comp] = json.loads(body)
            except Exception:
                pass

        self._page.on("response", on_response)
        try:
            await self._page.goto(url, wait_until="networkidle", timeout=60000)
            await self._page.wait_for_timeout(2500)
        finally:
            self._page.remove_listener("response", on_response)
        return collected

    async def _bill_detail(self, caid: str, invoice_id: str) -> dict:
        """Navigate one bill's ViewBill, parse service period + cost + daily usage."""
        url = VIEWBILL.format(caid=caid, inid=invoice_id)
        comps = await self._navigate_collect(url)

        bill = ibill.parse_meter_data(comps.get("meterdata", {}), invoice_id=invoice_id)
        if bill is None:
            bill = models.BillRecord(bill_date=None, amount=None, due_date=None,
                                     invoice_id=invoice_id)
        # cross-check / fill service period from ChargeDetails if MeterData missed it
        if (bill.service_period_start is None or bill.service_period_end is None) and "chargedetails" in comps:
            s, e = ibill.parse_charge_details_service_period(comps["chargedetails"])
            bill.service_period_start = bill.service_period_start or s
            bill.service_period_end = bill.service_period_end or e

        daily_key = next((k for k in comps if "meterdatadaily" in k), None)
        daily = ibill.parse_daily_usage(comps[daily_key]) if daily_key else []

        out = models.to_jsonable(bill)
        out["daily_usage"] = models.to_jsonable(daily)
        return out

    # ---- main orchestration ----------------------------------------------- #
    async def fetch_all(self, force: bool = False) -> dict:
        async with self._lock:
            await self._ensure_session()

            # dashboard: account, current bill, program flags
            await self._page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            await self._page.wait_for_timeout(1200)
            if not await self._is_logged_in():
                await self._login()
                await self._page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            html = await self._page.content()
            account = parsers.parse_account_info(html)
            current_bill = parsers.parse_current_bill(html)
            caid = account.contract_account_id

            flags: dict[str, bool | None] = {}
            for name, path in STATUS_ENDPOINTS.items():
                try:
                    raw = await self._page.evaluate(
                        """async (p) => { const r = await fetch(p,{method:'POST',credentials:'include',
                           headers:{'X-Requested-With':'XMLHttpRequest','Content-Type':'application/json'},body:'{}'});
                           const t = await r.text(); try { return JSON.parse(t);} catch(e){return t;} }""", path)
                    flags[name] = _coerce_flag(raw)
                except Exception:
                    flags[name] = None

            # enumerate bills: open the most-recent ViewBill to get BillSelector + monthly
            bills_list: list[dict] = []
            monthly: list = []
            if caid and current_bill.view_bill_url:
                inid0 = _inid_from_url(current_bill.view_bill_url)
                comps = await self._navigate_collect(
                    current_bill.view_bill_url if current_bill.view_bill_url.startswith("http")
                    else BASE + current_bill.view_bill_url)
                bills_list = ibill.parse_bill_selector(comps.get("billselector", {}))
                # monthly usage may load on this page too
                mk = next((k for k in comps if "monthly" in k), None)
                if mk:
                    monthly = ibill.parse_monthly_usage(comps[mk])

            # fetch any bills in the current window not already cached (or all if force).
            # NOTE: the cache is append-only — bills are never purged, so the archive
            # keeps growing and retains data even after TECO drops it from BillSelector.
            wanted = bills_list[:BACKFILL_BILLS] if bills_list else []
            for b in wanted:
                inid = b.get("invoice_id")
                if not inid or not caid:
                    continue
                if inid in self._cache and not force:
                    continue
                LOG.info("fetching bill %s (%s)", inid, b.get("label"))
                detail = await self._bill_detail(caid, inid)
                detail["bill_date"] = b.get("bill_date")
                detail["label"] = b.get("label")
                self._cache[inid] = detail
                _save_cache(self._cache)

            # assemble the response from the ENTIRE cache (full retained history)
            details, daily_clean = self._assemble_from_cache()
            return {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "account": models.to_jsonable(account),
                "current_bill": models.to_jsonable(current_bill),
                "flags": flags,
                "bills": details,
                "monthly_usage": models.to_jsonable(monthly),
                "daily_usage": daily_clean,
                "counts": {"bills": len(details), "daily": len(daily_clean),
                           "months": len(monthly), "archived_bills": len(self._cache)},
            }

    def _assemble_from_cache(self) -> tuple[list[dict], list[dict]]:
        """Build (bills, daily_usage) from the full append-only cache, newest first."""
        details: list[dict] = []
        daily_all: list[dict] = []
        for detail in self._cache.values():
            details.append({k: v for k, v in detail.items() if k != "daily_usage"})
            daily_all.extend(detail.get("daily_usage", []))
        details.sort(key=lambda d: (d.get("service_period_end") or d.get("bill_date") or ""),
                     reverse=True)
        # de-dupe daily by date (periods can share a boundary day)
        seen: set = set()
        daily_clean: list[dict] = []
        for d in sorted(daily_all, key=lambda x: x.get("date") or ""):
            k = d.get("date")
            if not k or k in seen:
                continue
            seen.add(k)
            daily_clean.append(d)
        return details, daily_clean

    async def reassemble_bill(self, invoice_id: str) -> dict:
        """Force re-fetch a single bill and update the cache."""
        async with self._lock:
            await self._ensure_session()
            await self._page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            html = await self._page.content()
            caid = parsers.parse_account_info(html).contract_account_id
            if not caid:
                raise RuntimeError("could not determine contract account id")
            detail = await self._bill_detail(caid, invoice_id)
            # keep label/bill_date if we already had them
            prev = self._cache.get(invoice_id, {})
            detail.setdefault("label", prev.get("label"))
            detail.setdefault("bill_date", prev.get("bill_date"))
            self._cache[invoice_id] = detail
            _save_cache(self._cache)
            return detail

    def export(self) -> dict:
        """Everything ever archived (full cache), for >3yr retention/export."""
        details, daily = self._assemble_from_cache()
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "archived_bills": len(self._cache),
            "bills": details,
            "daily_usage": daily,
        }

    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        finally:
            self._browser = self._ctx = self._page = self._pw = None
            self._logged_in_at = 0.0


def _inid_from_url(url: str) -> str | None:
    import re
    m = re.search(r"inid=ISU?([0-9]+)", url)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# HTTP API
# --------------------------------------------------------------------------- #
session = TecoSession()

try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse, HTMLResponse

    app = FastAPI(title="TECO auth+data sidecar", version="0.3.0")

    _UI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui.html")

    def _auth(request: Request, x_auth_token: str | None = Header(default=None)) -> None:
        """Require X-Auth-Token when SIDECAR_TOKEN is set — but exempt requests that
        arrive via Home Assistant ingress (already auth-gated by HA)."""
        if not TOKEN:
            return
        if request.headers.get("X-Ingress-Path") is not None:
            return
        if x_auth_token != TOKEN:
            raise HTTPException(status_code=401, detail="bad or missing X-Auth-Token")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Side-page billing dashboard (uses relative fetch -> works behind HA ingress)."""
        try:
            with open(_UI_PATH, encoding="utf-8") as f:
                return HTMLResponse(f.read())
        except FileNotFoundError:
            return HTMLResponse("<h1>TECO sidecar</h1><p>webui.html not found.</p>")

    @app.get("/health")
    async def health():
        return {"ok": True, "cached_bills": len(session._cache),
                "logged_in_age_s": int(time.time() - session._logged_in_at)
                if session._logged_in_at else None}

    @app.get("/data")
    async def data(force: bool = False, _=Depends(_auth)):
        try:
            return JSONResponse(await session.fetch_all(force=force))
        except Exception as e:
            LOG.exception("fetch_all failed")
            raise HTTPException(status_code=502, detail=str(e))

    @app.get("/bills")
    async def bills(_=Depends(_auth)):
        """List every archived bill (summary only) without a network fetch."""
        details, _u = session._assemble_from_cache()
        return {"archived_bills": len(details),
                "bills": [{k: b.get(k) for k in
                           ("invoice_id", "label", "bill_date", "service_period_start",
                            "service_period_end", "service_days", "kwh_used", "cost")}
                          for b in details]}

    @app.post("/reassemble")
    async def reassemble(invoice_id: str, _=Depends(_auth)):
        """Re-fetch and rebuild a single bill from TECO (force-refresh its detail)."""
        try:
            return JSONResponse(await session.reassemble_bill(invoice_id))
        except Exception as e:
            LOG.exception("reassemble failed")
            raise HTTPException(status_code=502, detail=str(e))

    @app.get("/export")
    async def export(format: str = "json", _=Depends(_auth)):
        """Export the full retained archive (all bills + daily usage), json or csv."""
        data = session.export()
        if format.lower() == "csv":
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["invoice_id", "label", "bill_date", "service_period_start",
                        "service_period_end", "service_days", "kwh_used", "cost",
                        "previous_reading", "current_reading", "meter_number"])
            for b in data["bills"]:
                w.writerow([b.get(k) for k in
                            ("invoice_id", "label", "bill_date", "service_period_start",
                             "service_period_end", "service_days", "kwh_used", "cost",
                             "previous_reading", "current_reading", "meter_number")])
            from fastapi.responses import Response
            return Response(content=buf.getvalue(), media_type="text/csv",
                            headers={"Content-Disposition":
                                     "attachment; filename=teco_bills.csv"})
        return JSONResponse(data)

    @app.on_event("shutdown")
    async def _shutdown():
        await session.close()
except ImportError:
    app = None  # FastAPI not installed (e.g. --once smoke test in a minimal env)


# --------------------------------------------------------------------------- #
# CLI: validate once without running the server
# --------------------------------------------------------------------------- #
async def _run_once(force: bool) -> int:
    try:
        payload = await session.fetch_all(force=force)
    finally:
        await session.close()
    c = payload["counts"]
    print(f"\nOK — bills={c['bills']} daily={c['daily']} months={c['months']}")
    b = payload["bills"][0] if payload["bills"] else {}
    print("newest bill:", json.dumps({k: b.get(k) for k in
          ("label", "service_period_start", "service_period_end", "service_days",
           "kwh_used", "cost")}, indent=1))
    out = os.path.join(CACHE_DIR, "last_payload.json")
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"full payload -> {out}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one fetch and exit")
    ap.add_argument("--force", action="store_true", help="ignore cache; refetch all bills")
    args = ap.parse_args()
    if args.once:
        raise SystemExit(asyncio.run(_run_once(args.force)))
    print("Run the server with:  uvicorn teco_auth_sidecar:app --host 0.0.0.0 --port 8089")
    print("Or validate once with: python teco_auth_sidecar.py --once")
