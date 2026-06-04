# TECO Billing — Home Assistant Add-on

Pulls Tampa Electric (TECO) billing, usage, cost, and service-period data into
Home Assistant and serves a billing dashboard in the sidebar. It logs into
`account.tecoenergy.com` in a headless browser (the only way past reCAPTCHA v3 +
Cloudflare + NetScaler) and keeps a **persistent, never-purged** archive of every
bill — so your history grows past TECO's ~3-year window.

## Install
1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add this repo URL.
2. Install **TECO Billing**.
3. Open the **Configuration** tab and enter:
   - **teco_user** — your TECO portal username
   - **teco_pass** — your TECO portal password (stored by Supervisor; shown as a password field)
   - **backfill_bills** — how many bills to pull on first run (default 36 ≈ 3 years)
   - **session_ttl_min** — re-login interval (default 30)
4. **Start** the add-on, then open it from the **TECO Billing** sidebar panel.

> **Changing your credentials?** The add-on reads `teco_user` / `teco_pass` from
> `/data/options.json` only at startup. After editing them on the Configuration
> tab, **restart the add-on** for the change to take effect.

The first start backfills all bills and can take a few minutes; later refreshes
are incremental (only new bills are fetched).

## The dashboard (sidebar panel)
- KPI cards: latest bill, latest usage, latest $/kWh, average $/kWh, archived totals
- Per-bill charts: kWh and cost over time
- Daily usage (last 90 days)
- A sortable table of every bill: bill date, **service period**, days, kWh, cost,
  **$/kWh**, and meter reads — with a per-row **re-assemble** button
- **Export CSV** button for the full archive

## Data captured per bill
`service_period_start/end`, `service_days`, `kwh_used`, `cost`, `cost_per_kwh`,
`previous_reading`, `current_reading`, `meter_number`, plus actual **daily kWh**.

## What it pushes into Home Assistant
The add-on talks to the HA Core API directly (`homeassistant_api: true`) — no HACS,
no MQTT:

- **Energy Dashboard** — daily **kWh** (`teco:energy_consumption`) and daily **cost**
  (`teco:energy_cost`) long-term statistics via `recorder/import_statistics`. On first
  run it **auto-wires** TECO as your grid source with the cost attached, so the
  dashboard shows **$ alongside kWh** with no manual setup (non-destructive — it won't
  touch an existing grid source; disable with the `setup_energy_dashboard` option).
- **Sensors** — `sensor.teco_amount_due`, `sensor.teco_last_bill_cost`,
  `sensor.teco_last_bill_rate` ($/kWh), `sensor.teco_service_period_start` / `_end`,
  `sensor.teco_service_days`, `sensor.teco_account_status`, plus
  `binary_sensor.teco_paperless` / `_autopay` / `_budget_billing` / etc., refreshed
  each poll via the REST states API.

## Notes
- **Run on your LAN.** reCAPTCHA v3 scores datacenter IPs harshly; Home Assistant
  on your home network logs in reliably.
- The archive lives in the add-on's persistent `/data/cache` and is never purged.
  Back up the add-on to preserve multi-year history.
- Credentials never leave this add-on; access to the panel/API is gated by Home
  Assistant ingress auth.

## Companion integration (optional)
The `custom_components/teco` HACS integration consumes this add-on's `/data` API to
create sensors and feed the Energy Dashboard (daily kWh + cost). See the repo root.
