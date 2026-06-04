#!/usr/bin/env python3
"""
Capture the InteractiveBill detail so we can locate, per bill:
  - service-period start/end (the metered window, NOT the bill date)
  - metered kWh for that window
  - cost for that window

The dashboard's Bill History only has bill date / amount / due date. The real
detail is behind each row's /InteractiveBill/ViewBill?caid=..&inid=.. link, which
may be HTML, a PDF, or a JS viewer backed by a JSON API. This script figures out
which, and dumps fixtures for offline parser work.

Run (same venv as the login PoC):
    export TECO_USER='...'; export TECO_PASS='...'
    python3 capture_bill.py

Outputs into fixtures/ (gitignored — contains PII):
    bill_detail_1.html / .pdf      first bill's detail (format auto-detected)
    bill_detail_1.network.txt      XHR/fetch the viewer made (to spot a JSON API)
    bill_detail_1.api_*.json       any JSON bill-data responses captured
"""
from __future__ import annotations

import asyncio
import os
import sys

from playwright.async_api import async_playwright

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "custom_components", "teco"))
import parsers  # noqa: E402

BASE = "https://account.tecoenergy.com"
LOGIN_URL = BASE + "/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
NBILLS = int(os.environ.get("NBILLS", "1"))  # how many bills to capture


async def main() -> int:
    user, pw = os.environ.get("TECO_USER"), os.environ.get("TECO_PASS")
    if not user or not pw:
        print("Set TECO_USER and TECO_PASS.", file=sys.stderr)
        return 2
    os.makedirs("fixtures", exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=os.environ.get("HEADLESS", "1") != "0",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = await browser.new_context(
            user_agent=UA, viewport={"width": 1440, "height": 900},
            locale="en-US", timezone_id="America/New_York", accept_downloads=True,
        )
        page = await ctx.new_page()

        # login
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
        await page.fill("#UserName", user)
        await page.fill("#Credentials_Password", pw)
        try:
            async with page.expect_navigation(wait_until="networkidle", timeout=60000):
                await page.click("#login-submit")
        except Exception:
            pass
        await page.wait_for_timeout(2500)
        body = (await page.inner_text("body")).lower()
        if "log off" not in body and "logout" not in body:
            print("[!] login failed."); await browser.close(); return 1
        print("[*] logged in.")

        # get bill view links from the dashboard
        html = await page.content()
        bills = parsers.parse_bill_history(html)
        links = [b.view_url for b in bills if b.view_url][:NBILLS]
        print(f"[*] found {len(bills)} bills; capturing {len(links)} detail page(s).")
        if not links:
            print("[!] no bill view links found."); await browser.close(); return 1

        for i, rel in enumerate(links, 1):
            url = rel if rel.startswith("http") else BASE + rel
            print(f"\n[*] bill {i}: {url.split('?')[0]}?...")

            # log network + capture every InteractiveBill ("ibill") API call in full
            net: list[str] = []
            captured_json: list[tuple[str, str, str]] = []  # (component, req_body, resp_body)

            async def on_response(resp):
                try:
                    u = resp.url
                    ct = (resp.headers.get("content-type") or "")
                    if "tecoenergy.com" in u or "miportal" in u:
                        net.append(f"{resp.status} {ct.split(';')[0]:20} {u}")
                    # the structured bill data: miportal .../api/ibill/.../Post/<Component>
                    if "miportal.tecoenergy.com/api/ibill" in u:
                        comp = u.rstrip("/").split("/")[-1]
                        try:
                            req_body = resp.request.post_data or ""
                        except Exception:
                            req_body = ""
                        body = await resp.text()
                        if len(body) < 500000:
                            captured_json.append((comp, req_body, body))
                except Exception:
                    pass

            page.on("response", on_response)
            is_pdf = False
            try:
                resp = await page.goto(url, wait_until="networkidle", timeout=60000)
                if resp and "pdf" in (resp.headers.get("content-type") or "").lower():
                    is_pdf = True
            except Exception as e:
                print(f"    [navigation note] {e}")
            await page.wait_for_timeout(3000)
            page.remove_listener("response", on_response)

            # save content
            if is_pdf:
                try:
                    pdf_bytes = await page.pdf()
                    open(f"fixtures/bill_detail_{i}.pdf", "wb").write(pdf_bytes)
                    print(f"    saved fixtures/bill_detail_{i}.pdf (PDF viewer detected)")
                except Exception as e:
                    print(f"    [pdf note] {e}")
            else:
                content = await page.content()
                open(f"fixtures/bill_detail_{i}.html", "w", encoding="utf-8").write(content)
                print(f"    saved fixtures/bill_detail_{i}.html ({len(content):,} bytes)")
                # also dump any iframe contents (interactive bill often lives in an iframe)
                for fi, frame in enumerate(page.frames):
                    if frame is page.main_frame:
                        continue
                    try:
                        fhtml = await frame.content()
                        if len(fhtml) > 1000:
                            open(f"fixtures/bill_detail_{i}.frame{fi}.html", "w",
                                 encoding="utf-8").write(fhtml)
                            print(f"    saved fixtures/bill_detail_{i}.frame{fi}.html "
                                  f"({len(fhtml):,} bytes) src={frame.url[:80]}")
                    except Exception:
                        pass

            open(f"fixtures/bill_detail_{i}.network.txt", "w").write("\n".join(net))
            print(f"    saved fixtures/bill_detail_{i}.network.txt ({len(net)} reqs)")
            for comp, req, b in captured_json:
                fn = f"fixtures/ibill_{comp}.json"
                open(fn, "w", encoding="utf-8").write(
                    f"// component: {comp}\n// request POST body: {req}\n{b}")
                print(f"    saved {fn}  <- ibill API ({len(b):,} bytes)")

        await browser.close()
    print("\n[*] done. Tell Claude and it will read the fixtures to write the bill-detail parser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
