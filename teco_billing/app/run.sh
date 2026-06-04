#!/usr/bin/env bash
# Read add-on options (Supervisor writes them to /data/options.json) and start the server.
set -euo pipefail

OPTS=/data/options.json
get() { python3 -c "import json;print(json.load(open('$OPTS')).get('$1', '$2'))" 2>/dev/null || echo "$2"; }

export TECO_USER="$(get teco_user '')"
export TECO_PASS="$(get teco_pass '')"
export BACKFILL_BILLS="$(get backfill_bills 36)"
export POLL_INTERVAL_HOURS="$(get poll_interval_hours 6)"
export SESSION_TTL_MIN="$(get session_ttl_min 30)"
export CACHE_DIR="/data/cache"          # persistent + never purged
# optional token: protects the exposed API port. Ingress (the panel) is exempt.
export SIDECAR_TOKEN="$(get auth_token '')"
export HEADLESS="1"
# SUPERVISOR_TOKEN is injected by Supervisor (homeassistant_api: true) -> HA publish

mkdir -p "$CACHE_DIR"

if [ -z "$TECO_USER" ] || [ -z "$TECO_PASS" ]; then
  echo "[teco] WARNING: set your TECO username/password on the Configuration tab."
fi

echo "[teco] starting (backfill=$BACKFILL_BILLS bills, poll=${POLL_INTERVAL_HOURS}h, ttl=${SESSION_TTL_MIN}m)"
exec uvicorn teco_auth_sidecar:app --host 0.0.0.0 --port 8089
