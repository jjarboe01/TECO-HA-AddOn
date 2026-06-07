# Changelog

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
