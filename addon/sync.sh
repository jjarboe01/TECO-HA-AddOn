#!/usr/bin/env bash
# Vendor the canonical app code into the add-on build context.
# Run from the repo root after changing the sidecar or parsers:
#   ./addon/sync.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/addon/teco_billing/app"
mkdir -p "$DEST"

cp "$ROOT/sidecar/teco_auth_sidecar.py"      "$DEST/"
cp "$ROOT/sidecar/webui.html"                "$DEST/"
cp "$ROOT/sidecar/requirements.txt"          "$DEST/"
cp "$ROOT/custom_components/teco/parsers.py" "$DEST/"
cp "$ROOT/custom_components/teco/models.py"  "$DEST/"
cp "$ROOT/custom_components/teco/ibill.py"   "$DEST/"
cp "$ROOT/addon/teco_billing/run.sh"         "$DEST/"

echo "synced app/ -> $DEST"
ls -1 "$DEST"
