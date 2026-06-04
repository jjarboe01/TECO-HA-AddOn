# TECO auth + data sidecar

The browser half of the TECO ↔ Home Assistant integration. It logs into
`account.tecoenergy.com` with a real (headless) browser — the only way past the
site's **reCAPTCHA v3 + Cloudflare + NetScaler** stack — fetches your dashboard
**inside that trusted browser context**, runs the verified parsers, and serves
clean JSON to Home Assistant.

Home Assistant stays a thin client: it polls `GET /data` and maps the result to
entities + Energy Dashboard statistics. No browser or Cloudflare exposure on the
HA side.

## Why a sidecar
- reCAPTCHA v3 is enforced at login → a browser must mint the token.
- Cloudflare can fingerprint/-challenge plain `aiohttp` calls even with valid
  cookies → all fetching happens in the browser the site already trusts.
- Chromium is heavy (~150 MB) and un-mergeable into HA core → isolate it in one
  container you control. Ideal on a homelab next to HA.

## Run (Docker, recommended)
```bash
cp .env.example .env          # fill in TECO_USER / TECO_PASS
docker compose up -d --build
curl localhost:8089/health
curl -H "X-Auth-Token: $SIDECAR_TOKEN" localhost:8089/data | jq
```

## Run (bare)
```bash
pip install -r requirements.txt
python -m playwright install chromium
export TECO_USER=... TECO_PASS=...
uvicorn teco_auth_sidecar:app --host 0.0.0.0 --port 8089
```

## API
Auth: send `X-Auth-Token: <SIDECAR_TOKEN>` when that env var is set.

| Route | Method | Returns |
|---|---|---|
| `/health` | GET | liveness, session age, archived-bill count |
| `/data` | GET | account + current bill + flags + **all archived bills** + daily/monthly usage. `?force=true` re-fetches every bill. |
| `/bills` | GET | summary of every archived bill (no network call) |
| `/reassemble?invoice_id=<id>` | POST | force re-fetch & rebuild one bill from TECO |
| `/export?format=json\|csv` | GET | the **entire retained archive** (all bills + daily usage); CSV downloads `teco_bills.csv` |

Validate without the server:
```bash
python teco_auth_sidecar.py --once          # one fetch; writes cache/last_payload.json
python teco_auth_sidecar.py --once --force   # ignore cache, refetch all bills
```

## Persistent archive (never purged)
Bills are cached on disk by invoice id and **never deleted**. `/data` and `/export`
are assembled from the *entire* cache, so your history keeps growing and is retained
even after TECO drops old bills from its ~3-year window. In Docker the cache lives on
the `teco_archive` volume (`CACHE_DIR=/data`) — **back it up** to preserve >3 years of data.
- First run backfills `BACKFILL_BILLS` bills (default 36 ≈ 3 yrs) — a few minutes.
- Later runs only fetch bills not already cached (incremental).
- `/reassemble` lets you rebuild any single bill; `/export?format=csv` dumps everything.

## Notes
- **Run on your LAN.** reCAPTCHA v3 scores datacenter IPs harshly; a residential
  egress IP (homelab) logs in reliably.
- Session is reused across requests and re-established when older than
  `SESSION_TTL_MIN` or when a fetch detects logout.
- Credentials live only in this container's env. Don't log them; don't commit `.env`.
