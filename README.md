# TECO ↔ Home Assistant

[![Validate](https://github.com/jjarboe01/TECO-HA-AddOn/actions/workflows/validate.yml/badge.svg)](https://github.com/jjarboe01/TECO-HA-AddOn/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fjjarboe01%2FTECO-HA-AddOn)

A **Home Assistant add-on** that brings **Tampa Electric (TECO)** billing, usage,
cost, and service-period data into Home Assistant — the **Energy Dashboard** at
**daily** resolution, **sensor entities**, a **persistent multi-year bill archive**,
and a sidebar **billing dashboard**.

The community had no working TECO solution: the portal is gated by reCAPTCHA v3 +
Cloudflare + NetScaler with no public API. This add-on solves it by logging in with
a headless browser (like a real user), driving TECO's InteractiveBill JSON APIs,
and pushing the results straight into Home Assistant.

## What you get
- **Energy Dashboard feed** — actual **daily kWh** (3 years backfilled) plus a
  parallel **daily cost** statistic distributed from each bill (reconciles to your
  real bills). Imported via HA's `recorder/import_statistics`.
- **Sensor entities** — amount due, due date, last bill cost/usage/**$ per kWh**,
  service period start/end/days, account status, and program flags
  (paperless, autopay, budget billing, SunSelect, Energy Planner, Prime Time Plus).
- **Per-bill detail** — service period, metered kWh, cost, absolute $/kWh, meter
  reads — aligned so usage matches each billing window.
- **Sidebar dashboard** — every archived bill in a sortable table, charts, CSV
  export, per-bill re-assemble.
- **Never-purged archive** — keeps every bill ever pulled, so history grows past
  TECO's ~3-year window.

No HACS and no MQTT required — it's a single add-on.

## Architecture
```
  TECO portal (reCAPTCHA v3 + Cloudflare + NetScaler)
        ▲  headless Chromium login + ibill JSON APIs
        │
  ┌──────────────────────────────┐
  │  TECO Billing add-on         │  ── ingress ─▶ sidebar billing dashboard
  │  (Playwright + FastAPI)      │
  │  • login + scrape + archive  │  ── HA Core API ─▶ Energy Dashboard statistics
  │  • push to Home Assistant    │                    (recorder/import_statistics)
  │  • /data, /export, /bills    │  ── REST states ─▶ sensor + binary_sensor entities
  └──────────────────────────────┘
        │  persistent /data/cache (never purged)
```
The add-on owns the browser (Cloudflare trusts it) and pushes data into HA itself
using the add-on's built-in HA API access (`homeassistant_api: true`). No separate
integration to install.

## Repository layout
| Path | What it is |
|---|---|
| `teco_billing/` | The **Home Assistant add-on** (this is the product): config, Dockerfile, `run.sh`, docs, and the vendored app in `app/`. |
| `sidecar/` | The app source — `teco_auth_sidecar.py` (engine), `ha_publish.py` (HA push), `parsers.py`/`ibill.py`/`models.py` (verified parsers), `webui.html` (dashboard). Also runs as a **standalone Docker** container. |
| `tests/` | Fixture-backed parser unit tests. |

## Install (Home Assistant add-on)
1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add this repo URL
   (or click the badge above).
2. Install **TECO Billing**.
3. **Configuration** tab → enter your TECO **username** and **password** (+ optional
   backfill depth / poll interval). **Start** the add-on.
4. Open the **TECO Billing** sidebar panel for the dashboard. Within a poll cycle,
   the **TECO** sensors appear under Devices & Services and `teco:energy_consumption`
   becomes available in **Settings → Dashboards → Energy**.

Full walkthrough: [`DEPLOY.md`](DEPLOY.md) · Add-on docs: [`teco_billing/DOCS.md`](teco_billing/DOCS.md)

## Standalone Docker (optional)
The same engine runs as a plain container (dashboard + JSON API, without the HA push):
```bash
cd sidecar
cp .env.example .env      # TECO_USER / TECO_PASS
docker compose up -d --build
# UI at http://<host>:8089 ; API: /data /export /bills /reassemble
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
After editing `sidecar/`, re-vendor the add-on app: `./sync.sh`.
See [`TECO_HA_Integration_Strategy.md`](TECO_HA_Integration_Strategy.md) for the full
reverse-engineering writeup (login contract, ibill API map, design decisions).

## Security & privacy
- Credentials live only in the add-on's config; never logged, never committed.
- Fixtures, cache, `.env`, and session files are gitignored (they contain PII).
- The dashboard/API is gated by Home Assistant ingress auth.

## Limitations
- Daily kWh is the finest resolution TECO exposes (no sub-daily interval data).
- REST-state sensors repopulate on each poll (they're not in the entity registry);
  the Energy Dashboard statistics are persistent.
- Login depends on TECO's portal markup/flow; if they change it, the parsers/login
  selectors may need an update. Parsers are fixture-tested to make that easy.
