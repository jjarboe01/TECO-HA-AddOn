# Deployment runbook — TECO Billing add-on (homelab)

The product is a single **Home Assistant add-on**. It runs the headless-browser
engine, pushes data into Home Assistant itself (Energy Dashboard + sensors), and
serves a sidebar dashboard. A standalone Docker option is included for non-HA hosts.

> ⚠️ **Run on your LAN.** reCAPTCHA v3 scores datacenter IPs harshly. Home Assistant
> on your home network logs in reliably; a cloud VM may get blocked.

---

## A. Install the add-on

### A1. Local add-on
1. Copy the add-on folder onto HA (via the **Samba** or **SSH/Advanced Terminal**
   add-on) so you have `/addons/teco_billing/config.yaml`:
   ```bash
   rsync -a addon/teco_billing/ root@<ha-host>:/addons/teco_billing/
   ```
2. **Settings → Add-ons → Add-on Store → ⋮ → Check for updates.** "TECO Billing"
   appears under **Local add-ons** → open it → **Install** (first build pulls the
   Playwright base image, a few hundred MB, one time).

### A2. (Alternative) from a Git repo
The add-on store expects `repository.yaml` + the add-on folder at the **repo root**.
To use this route, publish a repo whose root contains `repository.yaml` and
`teco_billing/` (the contents of `addon/`), then **Add-on Store → ⋮ → Repositories**
and paste the URL. (You can also click the "Add repository to my Home Assistant"
badge in the README.)

### A3. Configure & start
**Configuration** tab:
- **teco_user** / **teco_pass** — your TECO portal login
- **backfill_bills** — bills to pull on first run (default 36 ≈ 3 years)
- **poll_interval_hours** — how often to refresh + push to HA (default 6)
- **session_ttl_min** — re-login interval (default 30)
- **auth_token** — only needed if you expose the optional API port (see C)

**Start**, then watch the **Log** tab: `login OK` → `fetching bill …` → on the first
cycle, `import_statistics teco:energy_consumption: ok` and `updated N TECO sensor
entities`. The first backfill takes a few minutes; later polls are incremental.

> **Changing credentials?** The add-on reads them at startup — **restart** the add-on
> after editing them on the Configuration tab.

---

## B. What shows up in Home Assistant
- **Sidebar → TECO Billing** — the dashboard (bills, service periods, kWh, cost,
  $/kWh, meter reads; charts; CSV export; per-bill re-assemble).
- **Settings → Devices & Services → Entities** — `sensor.teco_amount_due`,
  `sensor.teco_last_bill_cost`, `sensor.teco_last_bill_rate` ($/kWh),
  `sensor.teco_service_period_start` / `_end`, `sensor.teco_account_status`,
  `binary_sensor.teco_paperless`, `…_autopay`, etc.
- **Settings → Dashboards → Energy → Add consumption** — pick the **TECO Energy**
  statistic (`teco:energy_consumption`). A `teco:energy_cost` statistic is also
  imported. External statistics can take a few hours to first appear; check
  **Developer Tools → Statistics**.

> REST-state sensors repopulate on each poll, so right after an HA restart they may
> read `unavailable` until the next cycle (or an add-on restart). The Energy
> Dashboard statistics are persistent and unaffected.

---

## C. Standalone Docker (non-HA host)
Runs the dashboard + JSON API only (no HA push, since there's no Supervisor):
```bash
cd sidecar
cp .env.example .env          # TECO_USER / TECO_PASS (+ SIDECAR_TOKEN to lock the API)
docker compose up -d --build
docker compose logs -f        # watch for "login OK"
```
UI at `http://<host>:8089/`; archive persists on the `teco_archive` volume — back it up.

---

## Verify
```bash
# add-on log shows: login OK, import_statistics ... ok, updated N TECO sensor entities
# in HA: Developer Tools → Statistics → search "teco"
# in HA: Developer Tools → States → filter "teco"
```

## Troubleshoot
| Symptom | Fix |
|---|---|
| `login failed (reCAPTCHA score or bad credentials)` | Run on your LAN (not a cloud IP); recheck user/pass on the Configuration tab; restart after changes. |
| Panel loads but empty | First backfill still running — watch the add-on Log, or click **Refresh from TECO**. |
| No sensors / no Energy stat | Confirm `homeassistant_api: true` is in the add-on (it is by default); check the Log for `import_statistics`/`updated … entities`; stats can lag a few hours. |
| Sensors show `unavailable` after restart | Expected for REST-state sensors until the next poll; trigger a restart or wait for the cycle. |
| First build is slow / large | Expected — the Playwright base image bundles Chromium (one-time). |

## Maintenance
- After editing the engine/parsers in `sidecar/`, re-vendor the add-on: `./addon/sync.sh`.
- Force-refresh one bill from the panel (↻) or `POST /reassemble?invoice_id=<id>`.
  Rebuild everything: `GET /data?force=true`.
- The archive is append-only and never purged; back up the add-on's `/data`.
