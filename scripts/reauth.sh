#!/usr/bin/env bash
set -euo pipefail

CLIENT_SECRET="${1:?usage: reauth.sh <client_secret.json>}"
REMOTE="${CALENDAR_SYNC_REMOTE:-macmini.internal}"
REMOTE_DIR="${CALENDAR_SYNC_REMOTE_DIR:-/home/darren/src/infra/calendar-sync}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${CALENDAR_SYNC_PYTHON:-$SCRIPT_DIR/../.venv/bin/python}"

AUTH_OUT=$("$PYTHON" "$SCRIPT_DIR/google_auth.py" "$CLIENT_SECRET")
echo "$AUTH_OUT"
TOKEN=$(echo "$AUTH_OUT" | grep -E '^GOOGLE_REFRESH_TOKEN=' | cut -d= -f2-)

[ -n "$TOKEN" ] || { echo "no refresh token captured" >&2; exit 1; }

echo "Updating $REMOTE:$REMOTE_DIR and restarting container"

ssh "$REMOTE" bash -s -- "$TOKEN" "$REMOTE_DIR" <<'REMOTE_EOF'
set -euo pipefail
TOKEN="$1"; DIR="$2"
cd "$DIR"
sed -i.bak "s|^GOOGLE_REFRESH_TOKEN=.*|GOOGLE_REFRESH_TOKEN=$TOKEN|" .env
rm -f .env.bak
export SOPS_AGE_KEY_FILE="$HOME/age-key.txt"
sops -e .env > .env.enc
docker compose up -d --force-recreate calendar-sync
REMOTE_EOF

echo "done. commit the updated .env.enc in the infra repo on macmini."
