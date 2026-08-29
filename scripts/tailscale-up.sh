#!/usr/bin/env bash
# One-shot Tailscale setup so the rate explorer is reachable from your phone
# (or laptop) when you're away from home — over a private tailnet, no public URL.
#
# Run this ONCE, at the desktop (or via Screen Sharing / SSH). It will:
#   1. install tailscaled as a system daemon (needs your password — one sudo)
#   2. bring the node up and print a login URL — open it on ANY device, sign in
#   3. point the frontend's API URL at this machine's tailnet name
#   4. restart the frontend container so the change takes effect
#
# Afterwards, from any device also signed into your tailnet:
#   http://<this-machine>.<your-tailnet>.ts.net:5173
#
# Requires: `brew install tailscale` (already done), Docker stack up (`make up`).

set -euo pipefail

TS=/opt/homebrew/opt/tailscale/bin/tailscale
TSD=/opt/homebrew/opt/tailscale/bin/tailscaled
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO/.env"

if [[ ! -x "$TS" ]]; then
  echo "tailscale not installed — run: brew install tailscale" >&2
  exit 1
fi

echo "==> Installing the tailscaled system daemon (sudo password prompt)…"
sudo "$TSD" install-system-daemon

echo "==> Bringing this node onto your tailnet…"
echo "    A login URL will print below — open it on your phone or laptop and sign in."
sudo "$TS" up

# MagicDNS short name for this machine, e.g. "mac-mini"
HOST="$("$TS" status --json | /usr/bin/python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["Self"]["DNSName"].rstrip("."))')"
if [[ -z "$HOST" ]]; then
  echo "Could not read this machine's tailnet name — check \`tailscale status\`." >&2
  exit 1
fi
echo "==> This machine is reachable on the tailnet as: $HOST"

echo "==> Pointing the frontend at $HOST (was: $(grep -E '^LAN_HOST=' "$ENV_FILE" || echo unset))"
if grep -qE '^LAN_HOST=' "$ENV_FILE"; then
  # portable in-place edit (BSD + GNU sed)
  sed -i.bak -E "s|^LAN_HOST=.*|LAN_HOST=$HOST|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
else
  printf '\nLAN_HOST=%s\n' "$HOST" >> "$ENV_FILE"
fi

echo "==> Restarting the frontend container so VITE_API_URL picks up the new host…"
cd "$REPO"
docker compose up -d --force-recreate frontend

cat <<EOF

Done. From any device signed into your tailnet:

    http://$HOST:5173

Notes:
  - The desktop must stay powered on and the Docker stack running.
  - CORS is already open ("*") and the ports bind to all interfaces, so no
    other changes are needed.
  - To go back to same-wifi-only: set LAN_HOST back to your LAN IP in .env
    and \`docker compose up -d --force-recreate frontend\`.
EOF
