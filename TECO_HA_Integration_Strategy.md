# TECO → Home Assistant Integration Strategy

**Project:** HACS custom integration for Tampa Electric (TECO) electric data
**Date:** 2026-06-03
**Status:** Strategy / pre-build research complete

---

## 1. TL;DR / Reality check

**Updated after live recon of `account.tecoenergy.com` (2026-06-03) — see Section 1a for confirmed findings.**

There is no off-the-shelf way to get TECO electric data into Home Assistant today — the HA community has been asking since 2023 with no working solution. Recon confirmed the data source reality, which rules out the easy paths:

1. **Oracle Opower — RULED OUT.** The portal fires **zero** requests to any `opower.com` host. TECO does not use Opower for usage data, so the `tronikos/opower` library and HA core `opower` integration are **not applicable**. (The embedded energy-audit/disaggregation tool is *Apogee Interactive*, not Opower, and only produces modeled estimates — not meter data.)
2. **Green Button "Download My Data" — NOT PRESENT.** No Green Button, CSV, or XML interval export was found anywhere in the authenticated portal. Cannot rely on it.
3. **Official TECO developer API — UNAVAILABLE.** The Azure APIM portal at `developer.tecoenergy.com` exposes no subscribable products to your account. The APIs.io OpenAPI spec is community-reconstructed by "api-evangelist" (Kin Lane), **not an official contract** — design reference only.

**What actually works (confirmed):** TECO runs a **custom ASP.NET MVC portal** with forms-auth cookie sessions and server-rendered pages that embed the data as inline Highcharts JS and HTML. The only viable integration is an **authenticated portal scraper** that logs in, pulls those pages, and parses out usage/billing/status.

**Recommended approach:** build a HACS custom integration that authenticates to `account.tecoenergy.com`, scrapes the confirmed endpoints (Section 1a), and injects monthly kWh into HA long-term statistics plus billing/status sensors. Keep the data layer abstracted (Section 4) so it can later swap to the official API if TECO ever ships one. **Top feasibility risk: reCAPTCHA on login** (the login page loads Google reCAPTCHA) — validate scripted login first (Section 9).

---

## 1b. Login contract — CONFIRMED (2026-06-03)

Inspected the unauthenticated login form (no credentials entered).

- **Endpoint:** `POST /Account/Login`
- **Form id:** `LoginForm`
- **Fields:**
  - `__RequestVerificationToken` — ASP.NET anti-forgery (hidden). Must be harvested from a fresh `GET /` along with its paired `__RequestVerificationToken` **cookie**; both must be sent back together.
  - `Credentials.UserName`
  - `Credentials.Password`
  - `as_fid` — hidden fingerprint field (Google fraud-detection); pre-populated, may or may not be enforced.
  - reCAPTCHA v3 token — injected at submit time into a hidden field by `grecaptcha.execute()`.
- **reCAPTCHA: v3 (score-based, invisible).** Confirmed by `https://www.google.com/recaptcha/api.js?render=6LfRyWoUAAAAAFKd8smIOIMf5OR8Dk9kxIMcRMsB` (the `?render=SITEKEY` form is the v3 signature) + submit-button class `recaptchav3`. **v3 never shows a user challenge** — it returns a 0.0–1.0 score and the server decides. This is the best-case captcha for automation (no image puzzles, no checkbox).
- **Session cookie:** standard ASP.NET forms-auth (e.g. `.ASPXAUTH` / `ASP.NET_SessionId`) set on successful login.

**Is the v3 score hard-enforced? → YES (confirmed 2026-06-03).**
`recon_login_test.py` attempted a token-less login: `POST /Account/Login` returned **200 with no redirect and no auth cookie** → login rejected. So **a valid reCAPTCHA v3 token is required**; pure-HTTP login does not work. Front-end stack confirmed: **Cloudflare** (`_cfuvid`) + **Citrix NetScaler** (`ns_session`, `te_sess_mgr*`) WAF/session layer in front of the app.

**Headless login PROVEN (2026-06-03).** `teco_login_poc.py` (Playwright, headless Chromium) logged in successfully on the first try — reCAPTCHA v3 + Cloudflare + NetScaler all passed headless, **no headed/xvfb needed**. It captured the session and extracted the 13-month kWh series. This validates the **headless-sidecar** architecture.

**Auth cookie = `.AspNet.ApplicationCookie`** (OWIN/ASP.NET Identity — *not* `.ASPXAUTH`). Full post-login cookie set: `.AspNet.ApplicationCookie`, `ASP.NET_SessionId`, `__RequestVerificationToken`, `ns_session`, `te_sess_mgr*` (×3), `_cfuvid`, `_GRECAPTCHA`, `ai_user`/`ai_session`, `X-Source`. The sidecar should hand HA the whole jar; the essential ones for authenticated GETs are `.AspNet.ApplicationCookie` + `ASP.NET_SessionId` + the `ns_session`/`te_sess_mgr*` NetScaler cookies.

**Consequence:** authentication needs a real browser context to mint the v3 token. Three viable patterns, in recommended order:

1. **Headless-browser auth sidecar (recommended given your homelab).** A tiny standalone service (Python + Playwright/Chromium, or Node) runs *next to* HA — not inside it. It performs the full browser login (fills creds → page's own `grecaptcha.execute()` mints the token → submits), captures the fresh session cookies, and exposes them to HA (e.g. `GET /session` → cookie bundle, or writes them to a shared path). The HA integration stays **pure-aiohttp**: it just consumes cookies and scrapes/parses. Heavy Chromium dependency is isolated to one container you control. Clean fit for a Proxmox/Docker homelab.
2. **Bundled Playwright inside the HA component.** Simplest single install, but pulls ~150 MB Chromium into the HA host, won't run on small devices, and is un-mergeable to HA core. Fine as a HACS custom component on a capable host.
3. **Manual session-cookie paste (zero-dependency fallback).** User logs in via browser, copies the `.ASPXAUTH` (+ NetScaler) cookies into the integration config. Works immediately with no browser dependency, but cookies expire (NetScaler idle timeout is typically short) → frequent manual re-auth. Good for a v0 / proof-of-life.

In all three, **data fetching is identical** (cookies → GET pages → parse). Only token/cookie acquisition differs — which is exactly why the architecture keeps auth behind the `TecoSource` abstraction.

**Re-auth handling:** detect expiry by a redirect-to-login or missing "Log off" in a fetched page; trigger a fresh sidecar login (pattern 1/2) or raise a re-auth repair flow (pattern 3).

---

## 1c. InteractiveBill JSON API — the real data source (CONFIRMED, verified)

The dashboard chart (daily-average kWh, monthly) was the *weak* source. The
**InteractiveBill** (`/InteractiveBill/ViewBill?caid=..&inid=..`) is backed by a
clean JSON API at **`https://miportal.tecoenergy.com/api/ibill/webcomponents/v1/Post/<Component>`**,
called from inside the authenticated browser. This **supersedes** the earlier
"monthly only" limitation. All parsers verified against captured fixtures
(`tests/test_ibill.py`, all pass):

| Component | Gives | Key fields |
|---|---|---|
| `BillSelector` | every bill (36 found, ~3 yrs) | `lable` (date), `value` (invoice id) |
| `MeterData` | **service period + reads + total kWh + $** | `DAP_StartDate`/`DAP_EndDate` (YYYYMMDD), `BillingPeriod` days, `TotalUsed` kWh, `BilledAmount`, `Previous/CurrentReading`, `MeterNumber`, `AMI_Flag` |
| `ChargeDetails` | itemized charges + service period (cross-check) | nested `Section` tree, "Service Period: mm/dd/yyyy - mm/dd/yyyy" |
| `meterDataMonthlyUsage` | **actual monthly kWh + cost** | `MonthlyDetails[]`: `FullDate`, `Usage`, `Cost`, `Days`, `Temperature` |
| `meterDataDailyUsage` | **actual daily kWh + temp** | `DailyDetails[]`: `FullDate`, `Usage`, `Temperature`, `status` (A=actual) |

**Verified reconciliation:** daily readings for the period sum to **4,121 kWh =
the bill's metered `TotalUsed`**, and `MeterData`/`ChargeDetails` agree on the
service window. `AMI_Flag=X` confirms a smart meter → daily granularity is real.

**Request notes (for the sidecar to replay in-browser):** most components POST
`{}` against the session's *selected* bill (set by navigating `ViewBill?...inid=`).
`meterDataDailyUsage` POSTs `{"dln","sdt","edt","intp":"D","dkwh":"x",...}` (dates
YYYYMMDD); `meterDataMonthlyUsage` carries `Contract/Dln/ZipCode/Operand1=HIST_KWH`
as query params. The sidecar enumerates bills via `BillSelector`, then pulls
`MeterData`+`ChargeDetails` per bill and monthly/daily usage for the feed.

**Energy Dashboard impact:** feed **daily kWh** statistics (HA rolls up to
monthly automatically) + a parallel **cost** statistic; expose per-bill
service-period/usage/cost as sensors + attributes so usage aligns to billed windows.

---

## 1a. Confirmed recon findings (2026-06-03, authenticated)

Captured by driving a logged-in browser session through `account.tecoenergy.com`.

**Platform:** Custom ASP.NET MVC app (server-rendered Razor views, `/bundles/...` script bundles, jQuery 3.4.1, Bootstrap 4.3.1, Highcharts 8.0.0). Auth = standard forms-auth session cookie. Owner footer: "Emera Inc." (TECO's parent). Live chat is Amazon Connect; surveys are Qualtrics; analytics via Azure App Insights + GTM. **Google reCAPTCHA bundle loads on the site** — flagged as login risk.

**Confirmed endpoints**

| Endpoint | Method | Returns | Use for |
|---|---|---|---|
| `/` (Home/Dashboard) | GET | HTML w/ inline Highcharts | Account #, address, status; **monthly daily-avg kWh (13 mo)**; current bill summary; bill & payment history tables |
| `/ContractAccount/ConsumptionHistory` | GET | redirects → `/energyaudit/billanalysis` | Usage analysis (Apogee, **modeled** cost/use disaggregation — not meter data) |
| `/ContractAccount/CurrentBill` | GET/POST | HTML (~63 KB) | Current bill detail |
| `/ContractAccount/BillPaymentHistory` | GET/POST | HTML (~101 KB) | Full bill + payment history |
| `/ContractAccount/GetAccountDesignationStatus` | GET | JSON-ish | Account designation |
| `/Dashboard/GetPaperlessBillingStatus` | POST | JSON | Paperless on/off |
| `/Dashboard/GetDirectDebitStatus` | POST | JSON | Autopay on/off |
| `/Dashboard/GetBudgetBillingStatus` | POST | JSON | Budget billing on/off |
| `/Dashboard/GetSunSelectStatus` | POST | JSON | SunSelect program |
| `/Dashboard/GetEnergyPlannerStatus` | POST | JSON | Energy Planner program |
| `/Dashboard/GetPrimetimePlusStatus` | POST | JSON | Prime Time Plus program |
| `/Dashboard/GetPowerUpdatesStatus` | POST | JSON | Outage/power updates |
| `/AccountInfo/UpdatePaperlessBillingEnrollment` etc. | POST | JSON | Enrollment writes (not needed for read-only) |

**Data granularity available (this is the constraint):**
- **Monthly billed kWh** and **daily-average kWh per billing month** — 13 months, embedded in the dashboard Highcharts series (confirmed values, e.g. Jun-2025 ≈ 150 daily-avg kWh → May-2026 ≈ 142).
- Per-bill totals (amount, due date, kWh) from bill history.
- **No hourly or daily interval (AMI) data exposed**, and no Green Button / raw-data download. The finest real consumption granularity is **monthly**.
- The `/energyaudit/` Apogee tool adds modeled end-use breakdowns (cooling/heating/water-heating %) and a "Download Report" (PDF) — useful as estimates only, ignore for the energy feed.

**Implications for the build:**
- The integration is a **scraper**: authenticate → GET dashboard + bill-history pages → parse inline Highcharts JSON + HTML tables; POST the `Get*Status` endpoints for account/program flags.
- Energy Dashboard will be fed at **monthly** resolution (still works via `async_add_external_statistics`, just coarse). Set expectations: this won't give hourly graphs, only month-over-month consumption + cost.
- Parsing inline `Highcharts.charts[...]` series is the cleanest extraction; fall back to regex over the inline `<script>` data block if markup shifts.

---

## 2. What the user wants surfaced

All four categories were requested. Mapped here to what the confirmed portal scraper can actually deliver:

| Category | Example entities | Availability via portal scrape |
|---|---|---|
| **Energy Dashboard (kWh)** | Monthly consumption fed to HA Energy Dashboard via external statistics | ✅ Monthly only (dashboard Highcharts). ❌ No hourly/daily interval data exists. |
| **Cost / billing** | Current balance, due date, last bill total, per-bill kWh, history | ✅ From dashboard + `CurrentBill` + `BillPaymentHistory`. |
| **Usage detail sensors** | Monthly kWh, daily-avg kWh, modeled end-use breakdown | ✅ Monthly + daily-avg. ⚠️ End-use split is an Apogee *estimate*, not metered. |
| **Account status** | Active/inactive, paperless, autopay, budget billing, programs | ✅ From `/Dashboard/Get*Status` JSON. Service requests: ❌ not in portal. |

Reality vs. the original ask: the Energy Dashboard works but at **monthly** resolution (no hourly graphs). Cost/billing and account-status are well covered. Ship all three together — they come from one authenticated session. Section 3's source ranking is **superseded by the confirmed recon in Section 1a**; it's retained for historical context.

---

## 3. Data-source assessment

### Option A — Oracle Opower (primary candidate)
**Pros:** Existing library does the heavy lifting (auth session handling, statistics shaping, cost + forecast); HA core integration is the reference implementation; hourly + daily + monthly reads; cost data; ~production quality. Adding a utility = one Python module.
**Cons:** Must reverse-engineer TECO's specific login/SSO flow (this is the only real work). Opower utilities vary — some use Basic auth, some OAuth/SAML/Okta, some MFA. MFA would be a blocker for unattended polling.
**Effort:** Low–medium **if** TECO's login is scriptable and MFA-optional.
**Confirm via:** recon spike (Section 9).

### Option B — Green Button "Download My Data" (reliable fallback)
**Pros:** Vendor-neutral ESPI XML; stable format; good historical backfill; no fragile scraping.
**Cons:** Typically a manual download (no documented automated "Connect My Data" OAuth for TECO confirmed); not real-time; you'd parse XML and inject via `async_add_external_statistics`. Good for backfill + periodic manual top-ups, weak for live dashboards.
**Effort:** Low for a one-shot importer; medium to automate the download.

### Option C — Official TECO API (watch, don't build yet)
**Pros:** If TECO ships real residential products on the APIM portal, this becomes the cleanest, ToS-friendly, supported path (bearer/JWT, clean JSON, `accounts`/`usage`/`bills` endpoints). It's the only source that cleanly provides **account status + service requests**.
**Cons:** Portal currently empty for you → not actually available. Spec is unofficial. Could be commercial/B2B only.
**Effort:** Unknown / blocked.
**Action:** Periodically re-check the portal's APIs/Products tabs; subscribe to a product the moment one appears. Keep the captured OpenAPI as the interface target (saved alongside this doc).

### Option D — Authenticated portal scraping (`account.tecoenergy.com`)
**Pros:** Works if Opower internal JSON endpoints are reachable after login (often they are — Opower SPAs call `…/opower.com/ei/edge/apis/...`). Gets account status/balance not in Opower lib.
**Cons:** Brittle (HTML/SPA changes), ToS-sensitive, MFA risk. Really this is "Option A done manually."
**Effort:** Medium, highest maintenance.

---

## 4. Recommended architecture

Build a standard HA config-entry integration with a **pluggable data-source layer** behind a single `DataUpdateCoordinator`. This lets you start on whichever source recon proves viable and swap/add others without touching the entity layer.

```
custom_components/teco/
├── __init__.py            # setup entry, create coordinator, forward platforms
├── manifest.json          # domain, deps (opower / lxml), iot_class, version
├── config_flow.py         # UI: pick source, enter creds/account, options
├── const.py               # DOMAIN, conf keys, source enum
├── coordinator.py         # TecoUpdateCoordinator -> calls active TecoSource
├── sources/
│   ├── base.py            # TecoSource ABC: async_get_usage(), get_account(), get_bills()
│   ├── portal_source.py   # PRIMARY: aiohttp login + scrape account.tecoenergy.com
│   │                      #   - login(): forms-auth POST, capture session cookie (+reCAPTCHA handling)
│   │                      #   - parse dashboard Highcharts -> monthly kWh
│   │                      #   - parse CurrentBill / BillPaymentHistory HTML
│   │                      #   - POST /Dashboard/Get*Status -> program/account flags
│   └── api_source.py      # FUTURE: official APIM client (stub vs captured OpenAPI), if TECO ships one
├── statistics.py          # async_add_external_statistics for Energy Dashboard
├── sensor.py              # balance, due date, last-bill, daily kWh, etc.
├── binary_sensor.py       # account active, paperless (if available)
├── strings.json / translations/
└── diagnostics.py         # redacted dump for debugging
```

**Why source-abstracted:** the four requested data categories straddle multiple sources. Opower gives energy+cost; account status needs API/scrape. An ABC (`sources/base.py`) with `async_get_usage()`, `async_get_account()`, `async_get_bills()` returning normalized dataclasses keeps `sensor.py` and `statistics.py` source-agnostic. Each source implements only what it can; the coordinator merges what's available and entities go `unavailable` when a field's source isn't active.

---

## 5. Home Assistant design details

### 5.1 Energy Dashboard (the important part)
HA's Energy Dashboard is fed by **long-term statistics**, not live sensor states, for utility data that arrives in hourly batches. Use **`homeassistant.components.recorder.statistics.async_add_external_statistics`** with:

- `statistic_id = "teco:energy_consumption"` (external stats use a `:` not a `.`).
- Hourly buckets, each carrying a monotonically increasing `sum` (cumulative kWh) plus `state`.
- `StatisticMetaData`: `has_sum=True`, `unit_of_measurement="kWh"`, `source="teco"`.
- This is exactly the pattern the core `opower` and `tibber` integrations use — copy it.

**Known limitation:** when an Energy source is an *external statistic*, the dashboard's built-in cost-tracking UI is disabled. To show cost, also publish a **cost external statistic** (`teco:energy_cost`, unit USD) — Opower provides per-interval cost, so add it as a parallel statistic and attach it as the source's cost stat. (This is the documented workaround for the "can't set cost on external stats" issue.)

### 5.2 Live sensors (`sensor.py`)
For the latest-known values (good for cards/automations, not the energy graph):
- `sensor.teco_balance` (device_class monetary, USD)
- `sensor.teco_amount_due` + `sensor.teco_due_date`
- `sensor.teco_last_bill_total`, `sensor.teco_last_bill_kwh`
- `sensor.teco_daily_usage` (yesterday's kWh; `state_class=total`)
- `sensor.teco_usage_to_date` / forecast (Opower provides a billing-period forecast)

### 5.3 Account status (`binary_sensor.py`, phase 2)
- `binary_sensor.teco_account_active`
- `binary_sensor.teco_paperless`
- Service requests → only if official API lands; model as sensor with attributes.

### 5.4 Config flow
- Step 1: choose source (Opower / Green Button file / Official API).
- Opower path: username, password, optional TOTP/MFA secret, account number selection (multi-account → one device per account).
- Store secrets in the config entry (HA encrypts at rest in `.storage`); never log them; redact in `diagnostics.py`.
- Options flow: poll interval (default 12h — utility data updates ~daily; don't hammer), enable/disable cost stat.

### 5.5 Coordinator cadence
Utility interval data lags 1–2 days and updates at most daily. Poll every 6–12h, not minutes. Backfill historical statistics on first run (Opower returns multi-month history; Green Button covers whatever range you export).

---

## 6. HACS packaging requirements

To be HACS-installable as a custom repo:

- **Repo layout:** integration under `custom_components/teco/`.
- **`manifest.json`** with `domain`, `name`, `version` (semver, required for custom), `documentation`, `issue_tracker`, `codeowners`, `iot_class` (`cloud_polling`), and `requirements` (e.g. `["opower>=0.x", "lxml>=5"]`).
- **`hacs.json`** at repo root: `{"name": "TECO (Tampa Electric)", "render_readme": true, "homeassistant": "2024.x.x"}` (set a sane minimum HA version).
- **`README.md`** + **`info.md`** (HACS install screen), GitHub **topics**, a **release** (HACS prefers tagged releases) or default-branch zip.
- Optional: GitHub Actions for `hassfest` + HACS validation (`home-assistant/actions`), `hacs/action`.
- License file. CODEOWNERS.

Long-term, if Opower-backed and clean, the better home may be upstreaming the TECO `Utility` subclass into `tronikos/opower` so it ships in **HA core's** opower integration — then no HACS needed at all. HACS is the right vehicle for the custom/experimental phase and for the non-Opower (API/scrape) bits core won't accept.

---

## 7. Auth & secrets

- Opower: username/password (+ possible MFA) stored in encrypted config entry.
- Official API: Azure APIM subscription key + likely OAuth2/JWT bearer — store key in config entry, refresh tokens via `aiohttp` session in the client.
- Never write creds to logs or diagnostics; use HA's `async_redact_data`.
- Respect TECO ToS — prefer the official API or Green Button over scraping where a choice exists; keep poll frequency low.

---

## 8. Phased roadmap

**Phase 0 — Recon spike (½–1 day).** Confirm the data source. See Section 9 checklist. Decision gate: Opower? API? Green Button only?

**Phase 1 — Skeleton + Energy Dashboard (1–2 days).** Scaffold `custom_components/teco`, config flow, coordinator, `sources/base.py`, and ONE working source. Get hourly kWh into the Energy Dashboard via external statistics. This alone solves the #1 community ask.

**Phase 2 — Cost + live sensors (1 day).** Cost external statistic; balance/due/last-bill sensors.

**Phase 3 — Account status (gated).** Only if official API or scrape provides it. Binary sensors + service-request sensor.

**Phase 4 — HACS polish.** README/info.md, hacs.json, releases, hassfest/HACS CI, diagnostics, tests.

**Phase 5 — Upstream (optional).** If Opower-backed and stable, PR the TECO utility module to `tronikos/opower` for HA core inclusion.

---

## 9. Immediate next steps — remaining validation

Recon is done (Section 1a). The data source is the portal scraper. One blocker must be cleared before committing to a build, then go.

1. **CLEAR THE BLOCKER — scripted login + reCAPTCHA.** The make-or-break item. Replicate the login `POST` with `aiohttp`/`httpx` outside the browser and confirm you get a valid session cookie. Inspect the login form for a reCAPTCHA token field:
   - **reCAPTCHA v3 (invisible/score)** → scripted login usually still works; proceed.
   - **reCAPTCHA v2 (checkbox/challenge)** → unattended login is blocked. Fallbacks: (a) user pastes a session cookie into config, integration refreshes pages until it expires; (b) periodic manual re-auth; (c) shelve until official API exists.
2. **Capture the exact login request.** Field names, hidden `__RequestVerificationToken` (anti-forgery), action URL, and the Set-Cookie name(s) (e.g. `.ASPXAUTH` / `ASP.NET_SessionId`). Mirror the browser headers.
3. **Lock the parsers.** Save one copy each of the dashboard, `CurrentBill`, and `BillPaymentHistory` HTML as parser fixtures. Confirm the monthly-kWh Highcharts series selector and the bill-table structure; write unit tests against the fixtures.
4. **Confirm multi-account behavior** if you have >1 contract account (account switcher / `MakeFavoriteContractAccount`) — model one HA device per account.
5. **Watch the official API.** Periodically re-check `developer.tecoenergy.com` Products tab; if a residential product appears, prefer it over scraping (ToS-cleaner) via the `api_source.py` slot.

---

## 10. Risks & open questions

- **MFA on TECO login** would break unattended Opower/scrape polling — may force Green Button (manual) or wait for the official API.
- **Official API may be B2B/commercial-only** or never opened to residential — don't block on it.
- **Opower module maintenance:** utility login flows change; expect occasional breakage (true of all ~20 supported utilities).
- **ToS / rate limiting:** keep polling ≥6h; prefer sanctioned sources.
- **Cost on Energy Dashboard:** external-statistic cost requires the parallel cost-stat workaround, not the built-in UI.

---

## Appendix — reference sources

- TECO API listing (community/unofficial spec): https://apis.apis.io/apis/teco-energy/account-api/
- Captured OpenAPI (reference only): saved as `teco-energy-account-openapi.yml` beside this doc
- TECO developer portal (Azure APIM): https://developer.tecoenergy.com/
- TECO account / usage portal: https://account.tecoenergy.com/ and https://account.tecoenergy.com/EnergyAudit
- HA community thread (no solution yet): https://community.home-assistant.io/t/anyone-from-tampa-fl-able-to-collect-grid-energy-usage-from-teco/649863
- `tronikos/opower` library: https://github.com/tronikos/opower
- HA core Opower integration: https://www.home-assistant.io/integrations/opower/
- HA external statistics how-to: https://community.home-assistant.io/t/how-to-use-external-statistics-sensor-energy-in-energy-dashboard/1012321
- Green Button: https://www.greenbuttondata.org/
