# Deployment runbook — TECO ↔ Home Assistant (homelab)

Three ways to deploy, from one engine. Pick **A (add-on)** for the simplest path on
a Home Assistant OS / Supervised install; **B (Docker)** if you'd rather run the
sidecar on another homelab host; both can pair with **C (HACS integration)** for
sensors + the Energy Dashboard.

> ⚠️ **Run the sidecar on your LAN.** reCAPTCHA v3 scores datacenter IPs harshly.
> A residential egress IP (your homelab) logs in reliably; a cloud VM may get blocked.

---

## A. Home Assistant add-on (recommended)

### A1. Install as a local add-on
1. Get the add-on folder onto HA (via the **Samba share** or **SSH/Advanced Terminal**
   add-on): copy `addon/teco_billing/` to the HA `/addons/` directory so you have
   `/addons/teco_billing/config.yaml`.
   ```bash
   # from this repo, e.g. over scp/rsync to the HA host's /addons share:
   rsync -a addon/teco_billing/ root@<ha-host>:/addons/teco_billing/
   ```
2. **Settings → Add-ons → Add-on Store → ⋮ (top-right) → Check for updates.**
   "TECO Billing" appears under **Local add-ons**.
3. Open it → **Install** (first build pulls the Playwright base image — a few hundred MB,
   one time).

### A2. (Alternative) install from a Git repo
The add-on store expects `repository.yaml` + the add-on folder at the **repo root**.
If you publish this, make a repo whose root contains `repository.yaml` and
`teco_billing/` (i.e. the contents of `addon/`). Then **Add-on Store → ⋮ → Repositories**
and paste the repo URL.

### A3. Configure & start
1. Open the add-on → **Configuration** tab:
   - **teco_user** / **teco_pass** — your TECO portal login
   - **auth_token** — optional; set it if you'll expose the API port (see C). Leave blank
     to keep the panel ingress-only.
   - **backfill_bills** (36 ≈ 3 yrs), **session_ttl_min** (30)
2. **Start**. Watch the **Log** tab: you should see `login OK` then `fetching bill …`.
   The first backfill takes a few minutes; later refreshes are incremental.
3. Open the **TECO Billing** panel in the sidebar — the billing dashboard.

The bill archive persists in the add-on's `/data/cache` and is **never purged**.
**Back up the add-on** to preserve >3 years of history.

---

## B. Docker container (other homelab host / Portainer)
```bash
cd sidecar
cp .env.example .env          # set TECO_USER / TECO_PASS (and SIDECAR_TOKEN if exposing)
docker compose up -d --build
docker compose logs -f        # watch for "login OK"
```
- UI: `http://<host>:8089/`
- API: `/data`, `/export?format=csv`, `/bills`, `POST /reassemble?invoice_id=…`
- Archive persists on the `teco_archive` Docker volume — **back it up**.

---

## C. HACS integration (sensors + Energy Dashboard)
The integration is a thin client that polls the add-on/sidecar — no browser in HA.

1. **HACS → ⋮ → Custom repositories** → add this repo, category **Integration** → install
   **TECO (Tampa Electric)**. Restart HA.
2. **Settings → Devices & Services → Add Integration → "TECO (Tampa Electric)"**.
3. **Sidecar URL:**
   - Add-on with the port exposed (default): `http://<HA-host-IP>:8089`
   - Docker host: `http://<docker-host-IP>:8089`
   - **Auth token:** only if you set `auth_token` / `SIDECAR_TOKEN`.
4. Entities appear under one **TECO (Tampa Electric)** device: amount due, due date,
   last bill cost/usage/**$ per kWh**, service period start/end/days, account status,
   and program binary sensors.

### Energy Dashboard
1. **Settings → Dashboards → Energy → Add consumption.**
2. Pick the **TECO Energy** statistic (`teco:energy_consumption`) as a grid source.
   (External statistics can take a few hours to first appear; check
   **Developer Tools → Statistics** for `teco:energy_consumption` and `teco:energy_cost`.)
3. Cost: a parallel `teco:energy_cost` statistic is published (daily, distributed from
   each bill). Note HA's built-in cost UI is limited for *external* statistics — view the
   cost statistic directly, or pair energy with a fixed price if you prefer.

---

## Verify
```bash
# add-on/docker reachable & logged in:
curl http://<host>:8089/health
# how many bills are archived (no network call):
curl http://<host>:8089/bills | jq '.archived_bills'
# full export:
curl "http://<host>:8089/export?format=csv" -o teco_bills.csv
```
In HA: the **TECO Billing** sidebar panel renders the dashboard; the **TECO** device
lists sensors; `teco:energy_consumption` shows in Developer Tools → Statistics.

## Troubleshoot
| Symptom | Fix |
|---|---|
| `login failed (reCAPTCHA score or bad credentials)` | Run on your LAN (not a cloud IP); double-check user/pass on the Configuration tab. |
| Panel/UI loads but empty | First backfill still running — watch the add-on Log; or click **Refresh from TECO**. |
| Integration "cannot connect" | Wrong URL/port, or `auth_token` set but not entered in the integration. `curl /health` from the HA host. |
| Energy Dashboard shows nothing yet | External statistics can lag a few hours; confirm the statistic exists in Developer Tools → Statistics. |
| First build is slow / large | Expected — the Playwright base image bundles Chromium (one-time). |

## Maintenance
- After editing the sidecar or parsers, re-vendor the add-on app: `./addon/sync.sh`.
- The archive is append-only; to force-refresh one bill use the panel's ↻ button or
  `POST /reassemble?invoice_id=<id>`. To rebuild everything: `GET /data?force=true`.
