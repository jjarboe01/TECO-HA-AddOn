#!/usr/bin/env python3
"""
TECO portal — headless login proof-of-concept (Playwright).

GOAL: prove that a real (headless) browser can:
  1. log into account.tecoenergy.com,
  2. satisfy reCAPTCHA v3 (the page's own grecaptcha.execute mints the token),
  3. get a forms-auth session (.ASPXAUTH) past the NetScaler/WAF layer,
  4. extract the session cookies (what the auth *sidecar* will hand to Home Assistant),
  5. fetch + parse the dashboard's monthly kWh + current-bill data.

If this works headless, the "headless sidecar" architecture is proven and the HA
component can stay pure-aiohttp (it just consumes the cookies this script captures).

------------------------------------------------------------------------------
SETUP (run locally — nothing is stored or sent anywhere but account.tecoenergy.com):
    python3 -m pip install playwright
    python3 -m playwright install chromium
    export TECO_USER='your_username'
    export TECO_PASS='your_password'
    # optional: HEADLESS=0 to watch it; reCAPTCHA v3 sometimes scores headless low,
    #           so if headless is blocked, try HEADLESS=0 first to confirm creds/flow.
    python3 teco_login_poc.py
------------------------------------------------------------------------------
Outputs:
  - prints a VERDICT (login ok? .ASPXAUTH present?)
  - prints parsed account summary + 13-month kWh series
  - writes teco_session.json (captured cookies) for the sidecar prototype
"""
from __future__ import annotations
import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

BASE = "https://account.tecoenergy.com"
LOGIN_URL = BASE + "/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# JS run in the page to pull the data we care about (no PII printed beyond what you own).
EXTRACT_JS = r"""
() => {
  const out = {};
  // --- monthly kWh from the dashboard Highcharts (daily-avg per billing month) ---
  try {
    const charts = (window.Highcharts && Highcharts.charts || []).filter(Boolean);
    const c = charts[0];
    if (c) {
      out.kwh = {
        categories: (c.xAxis && c.xAxis[0] && c.xAxis[0].categories) || null,
        values: (c.series[0].data || []).map(p => Number(p.y))
      };
    }
  } catch (e) { out.kwhErr = String(e); }

  // --- account + bill summary from the dashboard panels (best-effort label scrape) ---
  const grab = (labelRe) => {
    const el = [...document.querySelectorAll('td,div,span,label,p')]
      .find(n => labelRe.test((n.textContent || '').trim()));
    if (!el) return null;
    // value is usually the next sibling / following text
    const t = (el.parentElement ? el.parentElement.textContent : el.textContent) || '';
    return t.replace(/\s+/g, ' ').trim().slice(0, 120);
  };
  out.acct = {
    accountLine: grab(/Account\s*#/i),
    addressLine: grab(/Address/i),
    totalDueLine: grab(/Total amount due/i),
    dueDateLine: grab(/Due Date/i),
    billDateLine: grab(/Bill Date/i),
  };
  out.loggedIn = /log\s*off|logout/i.test(document.body.innerText);
  return out;
}
"""


def mask_acct(line: str | None) -> str | None:
    if not line:
        return line
    import re
    return re.sub(r"(\d{4})\d{4,}(\d{2})", r"\1****\2", line)


async def main() -> int:
    user = os.environ.get("TECO_USER")
    pw = os.environ.get("TECO_PASS")
    if not user or not pw:
        print("Set TECO_USER and TECO_PASS env vars first.", file=sys.stderr)
        return 2
    headless = os.environ.get("HEADLESS", "1") != "0"
    print(f"[*] launching Chromium (headless={headless}) ...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        ctx = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = await ctx.new_page()

        # 1) load login page, let reCAPTCHA v3 script initialize
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=45000)

        # 2) fill credentials (selectors confirmed from recon)
        await page.fill("#UserName", user)
        await page.fill("#Credentials_Password", pw)
        print("[*] credentials filled; submitting (page mints v3 token on submit) ...")

        # 3) submit and wait for the post-login navigation
        try:
            async with page.expect_navigation(wait_until="networkidle", timeout=45000):
                await page.click("#login-submit")
        except PWTimeout:
            print("[!] no navigation after submit (may have failed client-side).")

        # small settle for SPA/WAF redirects
        await page.wait_for_timeout(2500)

        # 4) verdict: cookies + page state
        cookies = await ctx.cookies()
        names = [c["name"] for c in cookies]
        # confirmed: TECO uses OWIN cookie auth -> ".AspNet.ApplicationCookie"
        AUTH_COOKIES = (".aspnet.applicationcookie", ".aspxauth", "fedauth")
        has_auth = any(any(a in n.lower() for a in AUTH_COOKIES) for n in names)
        url_now = page.url
        body = (await page.inner_text("body"))[:4000].lower()
        logged_in = "log off" in body or "logout" in body
        captcha_msg = any(w in body for w in ("recaptcha", "captcha", "verify you are human"))
        invalid = any(w in body for w in ("invalid", "incorrect", "does not match"))

        print("\n=== VERDICT ===")
        print(f"    url after login : {url_now}")
        print(f"    cookies         : {names}")
        print(f"    .ASPXAUTH set   : {has_auth}")
        print(f"    'Log off' shown : {logged_in}")
        if has_auth or logged_in:
            print("    RESULT          : SUCCESS — headless login works. Sidecar architecture viable.")
        elif captcha_msg:
            print("    RESULT          : BLOCKED by reCAPTCHA (low headless score).")
            print("                      Try HEADLESS=0; if that passes, add stealth or run the")
            print("                      sidecar headed/virtual-display. If even headed fails, recheck creds.")
        elif invalid:
            print("    RESULT          : credential error — check TECO_USER / TECO_PASS.")
        else:
            print("    RESULT          : UNCLEAR — inspect url/cookies/body above.")

        # 5) if logged in, save cookies (sidecar payload), dump HTML fixtures, extract data
        if has_auth or logged_in:
            with open("teco_session.json", "w") as f:
                json.dump(cookies, f, indent=2)
            print("\n[*] saved session cookies -> teco_session.json (sidecar would serve these)")

            # ensure we're on the dashboard so Highcharts is present
            if "energyaudit" in url_now or url_now.rstrip("/") != BASE:
                await page.goto(LOGIN_URL, wait_until="networkidle", timeout=45000)
                await page.wait_for_timeout(1500)

            # --- dump authoritative HTML fixtures for offline parser development ---
            # NOTE: these contain your account #, address, amounts -> DO NOT COMMIT.
            os.makedirs("fixtures", exist_ok=True)
            pages = {
                "dashboard": LOGIN_URL,
                "current_bill": BASE + "/ContractAccount/CurrentBill",
                "bill_history": BASE + "/ContractAccount/BillPaymentHistory",
            }
            for name, url in pages.items():
                try:
                    await page.goto(url, wait_until="networkidle", timeout=45000)
                    await page.wait_for_timeout(1200)
                    html = await page.content()
                    with open(f"fixtures/{name}.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    print(f"[*] saved fixtures/{name}.html ({len(html):,} bytes)")
                except Exception as e:  # noqa: BLE001
                    print(f"[!] could not capture {name}: {e}")

            # --- extract kWh from the dashboard (re-load to be safe) ---
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=45000)
            await page.wait_for_timeout(1500)
            data = await page.evaluate(EXTRACT_JS)
            kwh = data.get("kwh") or {}
            cats = kwh.get("categories")
            vals = kwh.get("values")
            print("\n=== EXTRACTED DATA ===")
            print(f"    kWh months ({len(cats or [])}): {cats}")
            print(f"    kWh values         : {vals}")
            print("    (billing/account parsing will be written against the saved fixtures)")
            if not cats:
                print("    [!] no Highcharts series found — dashboard markup may have changed.")

        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
