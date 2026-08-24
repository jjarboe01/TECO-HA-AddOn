# Changelog

## 1.3.2
- **Fix: after a restart, the gas account showed as "Electric" with kWh units** until the
  first poll finished. The service label lived only on cached bills, so a cache written by
  an older version had none, and `/export` filled the gap by assuming `electric` — which
  also made the dashboard default to the *gas* account (the first one that then looked
  electric). The account -> service map is now persisted to `accounts.json` beside the bill
  cache, so a restart knows kWh from therms before it talks to TECO.
- `/export` no longer guesses a service it does not know, and the dashboard renders an
  unlabelled account as "Account" with neutral units instead of asserting Electric/kWh.

## 1.3.1
- **Fix: gas bills were being plotted in the electric charts.** The sidebar dashboard
  loads `/export`, which spans every contract account, so once gas arrived in 1.3.0 its
  bills (therms) were drawn on the same axis as electric (kWh) — showing up as a row of
  tiny bars between the real ones, and dragging the summary line down to `min 1`. The
  dashboard now has an **account switcher** and shows one account at a time, with units,
  headings, table columns and tooltips following the selected service (kWh / $/kWh vs
  therms / $/therm). The daily-usage chart is hidden for gas, which has no daily data.
- Gas quantities render with one decimal — 4.6 therms was displaying as `5`.
- Each archived bill now records its `service`, so the split survives a restart; older
  cached bills are tagged in place on the next poll. `/export` gained an `accounts`
  summary and `primary_account`.
- The CSV export still spans every account by design (it is the full archive) and
  carries `account_id` per row.

## 1.3.0
- **Gas service (Peoples Gas) is now pulled alongside electric.** A login that has both
  accounts now fetches both each poll instead of only the selected one. Gas arrives as
  `sensor.teco_gas_*` entities (amount due, last bill cost, therms, CCF, $/therm, service
  period) and as its own **gas source on the Energy Dashboard**.
- **Gas usage is published in CCF**, taken from the meter reading delta
  (`CurrentReading - PreviousReading`). TECO bills gas in therms, which HA does not
  accept as a gas unit; CCF is the meter's own index, so no conversion factor is
  invented. The therm figure is still published on `sensor.teco_gas_last_bill_usage`.
- **Gas statistics are one point per billing period.** TECO publishes no daily gas
  readings at all (the gas ViewBill page loads `meterDataMonthlyUsage` and no daily
  component), so gas cannot be daily the way electric is.
- The bill archive is now tagged by contract account, and `/bills` + `/export` include
  an `account_id` column so gas and electric rows are distinguishable.

## 1.2.0
- **Fix: "login OK, no data" on logins with more than one account (#3).** TECO parks
  multi-account logins (e.g. Tampa Electric + Peoples Gas, or several premises) on an
  `/Selection` account picker instead of the dashboard. That page still has a "Log Off"
  link, so the session looked healthy while every dashboard selector and every
  `/Dashboard/*` endpoint failed — the add-on published 9 placeholder entities, imported
  zero statistics, and logged nothing. The picker is now handled: it selects the
  **electric** account automatically, or the one pinned by the new **account_id** option.
- **New option `account_id`** — contract account number to use when the picker offers
  several. Leave empty to auto-select the electric account.
- **No more silent no-op.** When the dashboard scrape comes back empty the add-on now
  logs the landing URL and what was missing, instead of skipping the bill fetch quietly.
- **Fix: `monthly_usage` was always empty** — the `meterDataMonthlyUsage` component was
  never added to the collected set, so the response was discarded before it was parsed.

## 1.1.1
- **Fix: daily usage (and the $/kWh chart) now update when a new bill posts.** TECO can
  publish a bill's total before its day-by-day readings are available; a bill fetched in
  that window was cached with empty/short daily and — because the archive is append-only
  — never refreshed, so daily got stuck at the last read. Each poll now re-fetches any
  bill whose daily is incomplete, so it fills in automatically once TECO posts the days.

## 1.1.0
- **Daily usage tracks the current (un-billed) period.** Each poll now also fetches any
  new days from TECO past the last bill — using the live session token — so the daily
  chart and Energy Dashboard advance as soon as TECO posts new readings, instead of
  waiting for the next bill to close. (TECO publishes daily readings with a lag; the
  add-on picks them up on its next poll once available.)

## 1.0.0
First stable release. 🎉 A single Home Assistant add-on that brings Tampa Electric
(TECO) billing, usage, cost, and service-period data into Home Assistant — verified
running end to end on a live install.

- **Energy Dashboard** — actual **daily kWh** (3 years backfilled) + a parallel
  **daily cost** statistic. Safe auto-wire that won't double-count an existing grid
  source; optional `grid_cost_from_teco` to use your real billed cost.
- **Sensors** — amount due, last bill cost/usage/$ per kWh, service period, account
  status, and program flags, kept alive by a 5-minute heartbeat (survive HA restarts).
- **Sidebar billing dashboard** — bills table; per-bill kWh, cost, and **$/kWh trend**
  charts with **y-axis scales**, **min·avg·max**, and hover tooltips; daily-usage chart;
  CSV export; per-bill re-assemble; wide layout for large monitors.
- **Never-purged archive** — every bill ever pulled is retained (history grows past
  TECO's ~3-year window).
- Built on a headless-browser engine that gets past **reCAPTCHA v3 + Cloudflare +
  NetScaler** (no public API). No HACS, no MQTT. Slim Chromium image.

## 0.6.1
- Dashboard: new **$/kWh trend** chart (per bill), zoomed so the small rate variance
  is readable, with a hover tooltip.
- Dashboard: **normalize all displayed dates** to `YYYY-MM-DD` so the newest bill no
  longer shows in a different format than the rest.

## 0.6.0
- **Energy Dashboard cost — correct & safe.** Uses HA's current flat grid schema.
  If you already have a grid source (e.g. a panel/CT monitor), it's left alone to
  avoid double-counting. New option **`grid_cost_from_teco`** (default off): when on,
  the add-on sets your existing grid source's cost to TECO's **actual billed cost**
  (`teco:energy_cost`), replacing any static $/kWh price.
- Graphs: chart axes now show **min · avg · max** (not just max).

## 0.5.0
- **Auto-wire the Energy Dashboard cost.** On first run the add-on attaches the
  `teco:energy_cost` statistic to the `teco:energy_consumption` grid source via the
  HA Energy preferences, so the dashboard shows **$ alongside kWh** with no manual
  setup. Non-destructive: only sets the cost on TECO's own source, and won't touch an
  existing grid source you've configured. New option **`setup_energy_dashboard`**
  (default on) to disable.
- Added this changelog.

## 0.4.3
- **Sensors stay alive.** A 5-minute sensor heartbeat re-posts the cached states,
  decoupled from the 6-hour TECO scrape — so entities refresh regularly and reappear
  within minutes of a Home Assistant restart (REST-state entities aren't persisted
  by HA). New option **`sensor_refresh_min`** (default 5).

## 0.4.2
- Web dashboard: rich **hover tooltips** on the per-bill kWh/cost and daily-usage
  charts (period, usage, cost, $/kWh, temperature).

## 0.4.1
- **Fixed Playwright browser mismatch** and slimmed the image: build on
  `python:3.12-slim` and install Chromium right after the pip package so the browser
  and library are always the same version. Image shrank from ~6 GB to roughly
  ~700 MB–1 GB. Dropped the deprecated `build.yaml`.

## 0.4.0
- Consolidated into a **single Home Assistant add-on** (no HACS, no MQTT). The add-on
  pushes data into HA itself via the Core API:
  - **Energy Dashboard** — daily kWh + daily cost imported via `recorder/import_statistics`.
  - **Sensors** — amount due, last bill cost/usage/$ per kWh, service period, account
    status, and program flags via the REST states API.
- Headless-browser engine: logs into `account.tecoenergy.com` past reCAPTCHA v3 +
  Cloudflare + NetScaler, drives the InteractiveBill JSON APIs, and keeps a
  **persistent, never-purged** bill archive (history grows past TECO's ~3-year window).
- Sidebar **billing dashboard** with per-bill detail, charts, CSV export, and per-bill
  re-assemble. Endpoints: `/data`, `/export`, `/bills`, `/reassemble`.
