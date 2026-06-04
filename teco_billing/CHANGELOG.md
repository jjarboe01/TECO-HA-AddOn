# Changelog

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
