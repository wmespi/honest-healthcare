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

set -euo pipefail

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

echo "==> Serving ports 5173 (frontend) and 8000 (API) on the tailnet…"
sudo "$TS" serve --bg --http=5173 http://127.0.0.1:5173
sudo "$TS" serve --bg --http=8000 http://127.0.0.1:8000

cat <<EOF

Done. From any device signed into your tailnet:

    http://$HOST:5173

Notes:
  - The desktop must stay powered on with the Docker stack running.
  - CORS is open ("*") and the frontend finds the API on port 8000 of whatever
    host you loaded it from — no rebuild needed.
  - Stop exposing it:  sudo $TS serve --http=5173 off; sudo $TS serve --http=8000 off
EOF
