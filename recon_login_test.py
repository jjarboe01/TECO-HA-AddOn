#!/usr/bin/env python3
"""
TECO portal login recon — does reCAPTCHA v3 get hard-enforced?

Confirmed login contract (2026-06-03):
    POST https://account.tecoenergy.com/Account/Login
    fields: __RequestVerificationToken (+ matching cookie), Credentials.UserName,
            Credentials.Password, as_fid (hidden), reCAPTCHA v3 token (hidden, injected at submit)
    reCAPTCHA: v3 score-based, invisible (sitekey 6LfRyWoUAAAAAFKd8smIOIMf5OR8Dk9kxIMcRMsB)

This script attempts a PURE-HTTP login WITHOUT minting a reCAPTCHA token.
  - If it SUCCEEDS  -> the v3 score is NOT hard-enforced. An HA integration can log in
                      with plain aiohttp/requests. Best case.
  - If it FAILS    -> the score is enforced. Integration will need a headless token-mint
                      step or a user-supplied session cookie.

Run locally (NOT committed anywhere). Credentials come from env vars:
    export TECO_USER='your_username'
    export TECO_PASS='your_password'
    python3 recon_login_test.py

Nothing is stored or transmitted anywhere except to account.tecoenergy.com.
"""
from __future__ import annotations
import os
import re
import sys
import requests

BASE = "https://account.tecoenergy.com"
LOGIN_PAGE = BASE + "/"
LOGIN_POST = BASE + "/Account/Login"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def extract_hidden(html: str, name: str) -> str | None:
    # <input name="X" type="hidden" value="..."> in any attribute order
    m = re.search(
        rf'<input[^>]*name=["\']{re.escape(name)}["\'][^>]*value=["\']([^"\']*)["\']',
        html, re.I)
    if m:
        return m.group(1)
    m = re.search(
        rf'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']{re.escape(name)}["\']',
        html, re.I)
    return m.group(1) if m else None


def main() -> int:
    user = os.environ.get("TECO_USER")
    pw = os.environ.get("TECO_PASS")
    if not user or not pw:
        print("Set TECO_USER and TECO_PASS env vars first.", file=sys.stderr)
        return 2

    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    # 1) GET login page -> harvest anti-forgery token (hidden field) + its cookie
    r = s.get(LOGIN_PAGE, timeout=30)
    r.raise_for_status()
    token = extract_hidden(r.text, "__RequestVerificationToken")
    as_fid = extract_hidden(r.text, "as_fid") or ""
    print(f"[*] GET / -> {r.status_code}; antiforgery field: "
          f"{'found' if token else 'MISSING'}; as_fid: "
          f"{'found' if as_fid else 'absent'}")
    print(f"[*] cookies after GET: {list(s.cookies.keys())}")
    if not token:
        print("[!] Could not find __RequestVerificationToken — markup may have changed.")
        return 1

    # 2) POST credentials WITHOUT any reCAPTCHA token
    payload = {
        "__RequestVerificationToken": token,
        "Credentials.UserName": user,
        "Credentials.Password": pw,
        "as_fid": as_fid,
        # intentionally NO 'g-recaptcha-response' / v3 token
    }
    headers = {
        "Referer": LOGIN_PAGE,
        "Origin": BASE,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
    }
    r2 = s.post(LOGIN_POST, data=payload, headers=headers,
                allow_redirects=False, timeout=30)
    print(f"[*] POST /Account/Login -> {r2.status_code}")
    print(f"[*] Location: {r2.headers.get('Location')}")
    print(f"[*] cookies after POST: {list(s.cookies.keys())}")

    auth_cookie = any(c.lower() in ("aspxauth", ".aspxauth") or "aspxauth" in c.lower()
                      or c == "FedAuth" for c in s.cookies.keys())
    body_low = (r2.text or "").lower()
    captcha_complained = any(w in body_low for w in
                             ("recaptcha", "captcha", "verify you are human", "robot"))
    invalid_creds = any(w in body_low for w in
                        ("invalid", "incorrect", "does not match", "try again"))

    # 3) Verdict: follow up by hitting an authed page
    r3 = s.get(BASE + "/", timeout=30, allow_redirects=True)
    logged_in = "log off" in r3.text.lower() or "logout" in r3.text.lower()

    print("\n=== VERDICT ===")
    if logged_in or auth_cookie:
        print("SUCCESS: token-less login worked. reCAPTCHA v3 is NOT hard-enforced.")
        print("=> HA integration can authenticate with plain HTTP. Best case.")
    elif captcha_complained:
        print("ENFORCED: server rejected login citing captcha. A v3 token is required.")
        print("=> Plan for headless token-mint OR user-supplied session cookie.")
    elif invalid_creds:
        print("AMBIGUOUS: looks like a credential error, not captcha. "
              "Double-check TECO_USER/TECO_PASS and re-run.")
    else:
        print("UNCLEAR: no auth cookie and no explicit captcha message. "
              "Inspect the printed status/Location/body manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
