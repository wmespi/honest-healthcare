#!/usr/bin/env bash
# Preview a branch or commit on an EPHEMERAL stack before promoting it to the
# tailnet — so you can see a change (a frontend redesign, say) at
# localhost:5183 without it reaching the people on your tailnet, and without a
# second stack sitting on your RAM all day.
#
#   scripts/preview.sh <ref>        # spin up ../hh-preview at <ref> on ports 5433/8010/5183
#   scripts/preview.sh --down       # stop it and free the RAM (worktree stays on disk, ~300 MB)
#   make preview REF=espinoza/feat/x
#   make preview-down
#
# LAN=1 binds 0.0.0.0 so you can open it from a laptop/phone at
# <this-host>.local:5183 (still not tailscale-served).
set -euo pipefail

canonical="$(git worktree list --porcelain | sed -n 's/^worktree //p' | head -1)"
dir="$(dirname "$canonical")/hh-preview"

if [ "${1:-}" = "--down" ]; then
  [ -d "$dir" ] || { echo "no preview stack running"; exit 0; }
  ( cd "$dir" && docker compose down )
  echo "preview stack stopped. remove the worktree entirely with:  git worktree remove --force $dir"
  exit 0
fi

ref="${1:?usage: preview.sh <ref> | --down}"
git -C "$canonical" fetch origin --prune -q

if [ ! -d "$dir" ]; then
  git -C "$canonical" worktree add --detach "$dir" HEAD
fi

cd "$dir"
if ! git checkout -q --detach "origin/$ref" 2>/dev/null && ! git checkout -q --detach "$ref" 2>/dev/null; then
  echo "unknown ref: $ref" >&2
  exit 1
fi
echo "preview at $(git rev-parse --short HEAD)  ($(git log -1 --format=%s))"

cat > .env <<EOF
COMPOSE_PROJECT_NAME=hh-preview
DB_PORT=5433
API_PORT=8010
WEB_PORT=5183
BIND_HOST=$([ "${LAN:-}" = "1" ] && echo 0.0.0.0 || echo 127.0.0.1)
VITE_API_URL=
HH_DATA_ROOT=$canonical/data
DUCKDB_MEMORY_LIMIT=2GB
EOF

docker compose up -d --build

host="localhost"
[ "${LAN:-}" = "1" ] && host="$(hostname).local"
echo
echo "✓ preview up:  http://$host:5183   (API http://$host:8010)"
echo "  stop it:  make preview-down     (frees the RAM; worktree stays for next time)"
