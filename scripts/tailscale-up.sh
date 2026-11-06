#!/usr/bin/env bash
# One-shot Tailscale setup so the app is reachable from your phone (or laptop)
# when you're away from home - over a private tailnet, no public URL.
#
# Run this ONCE, at the desktop (or via Screen Sharing / SSH). It will:
#   1. install tailscaled as a system daemon (needs your password - one sudo)
#   2. bring the node up and print a login URL - open it on ANY device, sign in
#   3. serve the app's port on the tailnet
#
# The app serves the screen, and the API under /api, from one port, so
# http://<this-machine>.<tailnet>.ts.net:8000 is the whole app.
#
# Requires: `brew install tailscale`, Docker stack up (`make up`).
#
# The port defaults to the canonical stack's (8000). To also expose a second
# stack (e.g. ../hh-staging), run again with its port:
#   TS_APP_PORT=8010 scripts/tailscale-up.sh

set -euo pipefail

APP_PORT="${TS_APP_PORT:-8000}"

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

echo "==> Serving port $APP_PORT on the tailnet…"
sudo "$TS" serve --bg --http="$APP_PORT" "http://127.0.0.1:$APP_PORT"

cat <<EOF

Done. From any device signed into your tailnet:

    http://$HOST:$APP_PORT

Notes:
  - The desktop must stay powered on with the Docker stack running.
  - Stop exposing it:  sudo $TS serve --http=$APP_PORT off
EOF
