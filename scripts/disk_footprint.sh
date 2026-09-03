#!/usr/bin/env bash
# Disk footprint of this project — the current worktree, every sibling worktree,
# the shared toolchains + git store, Docker, a headline TOTAL, and host free
# space. `make footprint`.
#
# Every PR body includes this output (AGENTS.md / docs/worktrees.md) so a runaway
# — a DuckDB query spilling into the repo, Docker build cache, an abandoned
# worktree — is caught the moment it lands, not months later.
set -euo pipefail
cd "$(dirname "$0")/.."
HERE="$PWD"
CANON="$(git worktree list --porcelain | sed -n 's/^worktree //p' | head -1)"

one()  { du -sh "$1" 2>/dev/null | cut -f1 || echo "-"; }
kb()   { du -sk "$1" 2>/dev/null | cut -f1 || echo 0; }
gib()  { awk -v k="$1" 'BEGIN{printf "%.1f GB", k/1048576}'; }

TOTAL_KB=0
add() { TOTAL_KB=$((TOTAL_KB + $(kb "$1"))); }

echo "## Disk footprint"
echo '```'

# ── this worktree ────────────────────────────────────────────────────────────
printf '%-26s %8s\n' "this worktree ($(basename "$HERE"))" "$(one "$HERE")"
for d in .venv frontend/node_modules data-test data-local .tmp .pytest_cache; do
  [ -e "$d" ] && printf '  %-24s %8s\n' "$d" "$(one "$d")"
done

# ── all worktrees (each du already includes its own data/ + .git) ────────────
echo
while IFS= read -r w; do
  [ -n "$w" ] || continue
  printf '%-26s %8s\n' "$(basename "$w")" "$(one "$w")"
  add "$w"
done < <(git worktree list --porcelain | sed -n 's/^worktree //p')
printf '%-26s %8s   (of that: %s)\n' "  ↳ canonical incl. data/" "$(one "$CANON")" \
  "$([ -d "$CANON/data" ] && echo "$(one "$CANON/data") parsed data" || echo "no data/")"
printf '%-26s %8s   (shared, one-time)\n' "mise toolchains" "$(one "$HOME/.local/share/mise")"
add "$HOME/.local/share/mise"

# ── Docker ───────────────────────────────────────────────────────────────────
echo
DOCK_KB=0
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  docker system df --format '{{printf "docker %-13s %9s  %s reclaimable" .Type .Size .Reclaimable}}' 2>/dev/null \
    | sed 's/^/  /'
  # project-attributable: honest-healthcare images + this project's volumes
  imgs=$(docker images --format '{{.Repository}} {{.Size}}' | awk '/honest-healthcare|hh-(etl|serving|frontend)/{print $2$3}')
  vols=$(docker system df -v 2>/dev/null | awk '/honest-healthcare_/{print $NF}')
  toB() { awk -v s="$1" 'BEGIN{n=s+0; if(s~/GB/)n*=1073741824; else if(s~/MB/)n*=1048576; else if(s~/kB/)n*=1024; printf "%d", n}'; }
  for x in $imgs $vols; do DOCK_KB=$((DOCK_KB + $(toB "$x")/1024)); done
  raw="$HOME/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw"
  [ -f "$raw" ] && printf '  %-20s %9s on disk (shared VM, all projects)\n' "Docker.raw" "$(one "$raw")"
  printf '  %-20s %9s\n' "↳ this project's share" "$(gib "$DOCK_KB")"
  printf '  running compose stacks: %s\n' "$(docker compose ls -q 2>/dev/null | wc -l | tr -d ' ')"
else
  echo "  (docker not running)"
fi
TOTAL_KB=$((TOTAL_KB + DOCK_KB))

# ── stray DuckDB spill (the Aug-29 176 GB leak lived here) ───────────────────
echo
spill=$(find "$(dirname "$CANON")" -maxdepth 3 \( -name '.tmp' -o -name 'duckdb_spill' -o -name '.duckdb_spill' \) -type d 2>/dev/null || true)
if [ -n "$spill" ]; then
  echo "⚠️  stray DuckDB spill (run: make clean):"
  echo "$spill" | while read -r s; do printf '    %8s  %s\n' "$(one "$s")" "$s"; done
else
  echo "stray DuckDB spill:         none ✓"
fi

# ── TOTAL + host volume ─────────────────────────────────────────────────────
echo
printf '%-26s %8s   (worktrees + mise + this project'"'"'s Docker)\n' "TOTAL project consumption" "$(gib "$TOTAL_KB")"
df -h /System/Volumes/Data 2>/dev/null | awk 'NR==2{printf "host volume:                %s used / %s free  (%s of %s)\n", $3,$4,$5,$2}' \
  || df -h . | tail -1
echo '```'
