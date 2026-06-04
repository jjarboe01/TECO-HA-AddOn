# TECO ↔ Home Assistant

Bring **Tampa Electric (TECO)** billing, usage, cost, and service-period data into
Home Assistant — including the **Energy Dashboard** at **daily** resolution, a
**persistent multi-year bill archive**, and a sidebar **billing dashboard**.

The community had no working TECO solution (the portal is gated by reCAPTCHA v3 +
Cloudflare + NetScaler, with no public API). This project solves it with a small
headless-browser **sidecar** that logs in like a real browser, drives TECO's
InteractiveBill JSON APIs, and serves clean data to Home Assistant.

## What you get
- **Energy Dashboard feed:** actual **daily kWh** (3 years backfilled) + a parallel
  **daily cost** statistic distributed from each bill — reconciles to your real bills.
- **Per-bill detail:** service period (start/end), days, metered kWh, cost,
  **absolute $/kWh**, meter reads — aligned so HA usage matches each billing window.
- **Sensors:** amount due, due date, last bill cost/usage/$ per kWh, service period,
  account status; **binary sensors** for paperless, autopay, budget billing, SunSelect,
  Energy Planner, Prime Time Plus, power updates.
- **Sidebar dashboard:** every archived bill in a sortable table, charts, CSV export,
  per-bill re-assemble.
- **Never-purged archive:** keeps every bill ever pulled, so history grows past TECO's
  ~3-year window.

## Architecture
```
  TECO portal (reCAPTCHA v3 + Cloudflare + NetScaler)
        ▲  headless Chromium login + ibill JSON APIs
        │
  ┌─────────────┐   GET /data (JSON)   ┌──────────────────────────┐
  │   Sidecar   │ ───────────────────► │  HA integration (HACS)   │ ─► sensors
  │ (Playwright)│   GET /export, etc.  │  custom_components/teco  │ ─► Energy Dashboard
  │  + web UI   │ ◄─────────────────── │  (pure aiohttp, no browser)
  └─────────────┘                      └──────────────────────────┘
        │  persistent /data/cache (never purged)
```
The sidecar owns the browser (Cloudflare trusts it); Home Assistant stays a thin
client. Same engine ships three ways.

## Components
| Path | What it is |
|---|---|
| `sidecar/` | Standalone Docker service (FastAPI + Playwright) + web UI. `docker-compose.yml` included. |
| `addon/teco_billing/` | The same service packaged as a **Home Assistant add-on** (Configuration tab for credentials, ingress sidebar dashboard). |
| `custom_components/teco/` | **HACS integration** — sensors + Energy Dashboard statistics that consume the sidecar. |

## Install (recommended: add-on + HACS integration)
1. **Add-on:** add this repo under Settings → Add-ons → Add-on Store → ⋮ → Repositories,
   install **TECO Billing**, enter your TECO username/password on the **Configuration**
   tab, start it. Open the **TECO Billing** sidebar panel. (See `addon/teco_billing/DOCS.md`.)
2. **Integration (optional, for sensors + Energy Dashboard):** install
   `custom_components/teco` via HACS (custom repo), add the **TECO (Tampa Electric)**
   integration, and point it at the add-on URL (e.g. `http://<homeassistant>:8089`).
   Add the `teco:energy_consumption` statistic as an Energy Dashboard source.

### Or run the sidecar as plain Docker
```bash
cd sidecar
cp .env.example .env      # TECO_USER / TECO_PASS
docker compose up -d --build
# UI at http://<host>:8089 , API at /data /export /bills /reassemble
```

> **Run on your LAN.** reCAPTCHA v3 scores datacenter IPs harshly; Home Assistant on
> your home network logs in reliably.

## Development / verification
Pure parsers are unit-tested against captured fixtures (no browser needed):
```bash
python3 tests/test_parsers.py     # dashboard HTML parsers
python3 tests/test_ibill.py       # InteractiveBill JSON parsers
python3 sidecar/teco_auth_sidecar.py --once   # full live backfill -> cache/last_payload.json
```
See `TECO_HA_Integration_Strategy.md` for the full reverse-engineering writeup
(login contract, ibill API map, design decisions).

## Security & privacy
- Credentials live only in the sidecar/add-on env; never logged, never committed.
- Fixtures, cache, `.env`, and session files are gitignored (they contain PII).
- The add-on panel/API is gated by Home Assistant ingress auth.

## Limitations
- Daily kWh is the finest resolution TECO exposes (no sub-daily interval data).
- Login depends on TECO's portal markup/flow; if they change it, the parsers/login
  selectors may need an update. Parsers are fixture-tested to make that easy.
