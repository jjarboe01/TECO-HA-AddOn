#!/usr/bin/env python3
"""
Capture the exact `meterDataDailyUsage` request the InteractiveBill makes, then test
pulling the CURRENT (in-progress, un-billed) period so we can keep daily usage current
between bills.

Run (same venv as the other scripts):
    export TECO_USER='...'; export TECO_PASS='...'
    python3 capture_current_usage.py

Outputs (gitignored — contain account data):
    fixtures/daily_request.json     exact url + headers + body of the daily-usage call
    fixtures/current_usage.json     result of fetching <last bill end+1 .. today>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta

from playwright.async_api import async_playwright

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sidecar"))
import parsers  # noqa: E402
import ibill    # noqa: E402

BASE = "https://account.tecoenergy.com"
LOGIN_URL = BASE + "/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
DAILY_MATCH = "meterdatadailyusage"


async def main() -> int:
    user, pw = os.environ.get("TECO_USER"), os.environ.get("TECO_PASS")
    if not user or not pw:
        print("Set TECO_USER and TECO_PASS.", file=sys.stderr); return 2
    os.makedirs("fixtures", exist_ok=True)

    async with async_playwright() as p:
        b = await p.chromium.launch(headless=os.environ.get("HEADLESS", "1") != "0",
                                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = await b.new_context(user_agent=UA, viewport={"width": 1440, "height": 900},
                                  locale="en-US", timezone_id="America/New_York")
        page = await ctx.new_page()

        # login
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
        await page.fill("#UserName", user); await page.fill("#Credentials_Password", pw)
        try:
            async with page.expect_navigation(wait_until="networkidle", timeout=60000):
                await page.click("#login-submit")
        except Exception:
            pass
        await page.wait_for_timeout(2500)
        if "log off" not in (await page.inner_text("body")).lower():
            print("[!] login failed"); await b.close(); return 1
        print("[*] logged in.")

        html = await page.content()
        caid = parsers.parse_account_info(html).contract_account_id
        bills = parsers.parse_bill_history(html)
        view = next((x.view_url for x in bills if x.view_url), None) \
            or (parsers.parse_current_bill(html).view_bill_url)
        if not view:
            print("[!] no bill view link"); await b.close(); return 1
        url = view if view.startswith("http") else BASE + view

        # capture the daily-usage request fired by the bill page
        captured = {}
        meterdata = {}

        async def on_response(resp):
            u = resp.url.lower()
            if "miportal.tecoenergy.com/api/ibill" not in u:
                return
            if DAILY_MATCH in u and "request" not in captured:
                try:
                    req = resp.request
                    captured["url"] = resp.url
                    captured["method"] = req.method
                    captured["headers"] = await req.all_headers()
                    captured["post_data"] = req.post_data
                    captured["response_status"] = resp.status
                except Exception as e:
                    captured["err"] = str(e)
            if u.rstrip("/").split("/")[-1].split("?")[0] == "meterdata" and not meterdata:
                try:
                    meterdata.update(await resp.json())
                except Exception:
                    pass

        page.on("response", on_response)
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3500)
        page.remove_listener("response", on_response)

        if "url" not in captured:
            print("[!] did not see a meterDataDailyUsage request"); await b.close(); return 1
        with open("fixtures/daily_request.json", "w") as f:
            json.dump(captured, f, indent=1)
        print(f"[*] captured daily request -> fixtures/daily_request.json "
              f"(status {captured.get('response_status')})")

        # pull dln + last service end from MeterData
        bill = ibill.parse_meter_data(meterdata) if meterdata else None
        dln = None
        for m in (meterdata.get("MeterTabel") or []):
            if str(m.get("Service", "")).lower() == "electric":
                dln = str(m.get("DLN") or "")
        last_end = bill.service_period_end if bill else None
        print(f"[*] dln={dln!r}  last_service_end={last_end}")

        # build current-period dates: day after last bill end -> yesterday
        today = date.today()
        start = (last_end + timedelta(days=1)) if last_end else (today - timedelta(days=45))
        sdt, edt = start.strftime("%Y%m%d"), today.strftime("%Y%m%d")
        print(f"[*] requesting current period {sdt}..{edt}")

        # replicate the captured request with new dates, in the page context
        body = {}
        try:
            body = json.loads(captured.get("post_data") or "{}")
        except Exception:
            body = {}
        body.update({"dln": dln or body.get("dln", ""), "sdt": sdt, "edt": edt,
                     "intp": "D", "dkwh": "x"})
        # strip the BilledAmount query param (no bill for the current period)
        api_url = captured["url"].split("?")[0]
        # reuse the session auth headers the ibill component sent (Bearer JWT etc.)
        hh = captured.get("headers", {})
        auth = {k: hh[k] for k in ("authorization", "usercredentials", "iscollectiveaccount")
                if k in hh}

        async def fetch_range(sdt2, edt2):
            bb = dict(body); bb["sdt"] = sdt2; bb["edt"] = edt2
            return await page.evaluate(
                """async ({url, body, auth}) => {
                    try {
                        const h = Object.assign({'Content-Type':'application/json'}, auth);
                        const r = await fetch(url, {method:'POST', credentials:'include',
                            headers:h, body: JSON.stringify(body)});
                        const t = await r.text(); let j=null; try{ j=JSON.parse(t);}catch(e){}
                        return {status:r.status, json:j, text:(j?null:t.slice(0,200))};
                    } catch(e){ return {error:String(e)}; }
                }""", {"url": api_url, "body": bb, "auth": auth})

        bend = last_end or date(today.year, today.month, 1)
        probes = {
            "billed_period": ((bend - timedelta(days=28)).strftime("%Y%m%d"), bend.strftime("%Y%m%d")),
            "overlap_to_today": ((bend - timedelta(days=14)).strftime("%Y%m%d"), edt),
            "last_60_days": ((today - timedelta(days=60)).strftime("%Y%m%d"), edt),
            "current_only": (sdt, edt),
        }
        report = {}
        print("\n=== probing ranges ===")
        for name, (a, z) in probes.items():
            r = await fetch_range(a, z)
            det = (((r or {}).get("json") or {}).get("DailyUsage") or {}).get("DailyDetails") or []
            last = det[-1].get("FullDate") if det else None
            report[name] = {"range": f"{a}..{z}", "status": r.get("status"),
                            "days": len(det), "last_day": last}
            print(f"  {name:18} {a}..{z}  HTTP {r.get('status')}  days={len(det)}  last={last}")

        with open("fixtures/current_usage.json", "w") as f:
            json.dump({"request_url": api_url, "probes": report}, f, indent=1)
        print("    full -> fixtures/current_usage.json")
        await b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
