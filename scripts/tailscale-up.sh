#!/usr/bin/env bash
# One-shot Tailscale setup so the rate explorer is reachable from your phone
# (or laptop) when you're away from home — over a private tailnet, no public URL.
#
# Run this ONCE, at the desktop (or via Screen Sharing / SSH). It will:
#   1. install tailscaled as a system daemon (needs your password — one sudo)
#   2. bring the node up and print a login URL — open it on ANY device, sign in
#   3. serve the frontend + API ports on the tailnet
#
# The frontend derives its API URL from window.location at runtime
# (frontend/src/api.js), so no rebuild or .env change is needed — opening
# http://<this-machine>.<tailnet>.ts.net:5173 just works.
#
# Requires: `brew install tailscale`, Docker stack up (`make up`).
#
# Ports default to the canonical stack (5173 / 8000). To also expose a second
# stack (e.g. ../hh-staging), run again with its ports:
#   TS_WEB_PORT=5183 TS_API_PORT=8010 scripts/tailscale-up.sh
# (the staging frontend needs VITE_API_URL set to its own API port — see .env.example.)

set -euo pipefail

WEB_PORT="${TS_WEB_PORT:-5173}"
API_PORT="${TS_API_PORT:-8000}"

TS=/opt/homebrew/opt/tailscale/bin/tailscale
TSD=/opt/homebrew/opt/tailscale/bin/tailscaled

if [[ ! -x "$TS" ]]; then
  echo "tailscale not installed — run: brew install tailscale" >&2
  exit 1
fi

echo "==> Installing the tailscaled system daemon (sudo password prompt)…"
sudo "$TSD" install-system-daemon

echo "==> Bringing this node onto your tailnet…"
echo "    A login URL will print below — open it on your phone or laptop and sign in."
sudo "$TS" up

HOST="$("$TS" status --json | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
[[ -n "$HOST" ]] || { echo "Could not read this machine's tailnet name — check \`tailscale status\`." >&2; exit 1; }

echo "==> Serving ports $WEB_PORT (frontend) and $API_PORT (API) on the tailnet…"
sudo "$TS" serve --bg --http="$WEB_PORT" "http://127.0.0.1:$WEB_PORT"
sudo "$TS" serve --bg --http="$API_PORT" "http://127.0.0.1:$API_PORT"

cat <<EOF

Done. From any device signed into your tailnet:

    http://$HOST:$WEB_PORT

Notes:
  - The desktop must stay powered on with the Docker stack running.
  - CORS is open ("*") and the frontend finds the API on port $API_PORT of whatever
    host you loaded it from — no rebuild needed (unless API_PORT != 8000, then set
    VITE_API_URL for that stack).
  - Stop exposing it:  sudo $TS serve --http=$WEB_PORT off; sudo $TS serve --http=$API_PORT off
EOF
