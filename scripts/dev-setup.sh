#!/usr/bin/env bash
# Set up host toolchains + per-worktree Docker config for parallel development
# (GH #59). Idempotent — safe to re-run.
#
#   scripts/dev-setup.sh [--no-mise] [--rebuild-reference]
#
#   --no-mise             use whatever python3 / node / go are on PATH instead of
#                         installing pinned versions via mise
#   --rebuild-reference   this worktree will regenerate reference/CMS/summary
#                         parquet — seed ./data-local from the shared store and
#                         append the sub-store split (REFERENCE_DIR/…) to .env
#
# The container-only quickstart (README.md) needs none of this.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

NO_MISE=0
REBUILD_REF=0
for a in "$@"; do
  case "$a" in
    --no-mise) NO_MISE=1 ;;
    --rebuild-reference) REBUILD_REF=1 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
  esac
done

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!  \033[0m %s\n' "$*" >&2; }

# ── canonical checkout (first worktree git knows about) ──────────────────────
CANONICAL="${HH_CANONICAL:-$(git worktree list --porcelain | sed -n 's/^worktree //p' | head -1)}"
IS_CANONICAL=0
[ "$ROOT" = "$CANONICAL" ] && IS_CANONICAL=1

# ── 1. toolchains ───────────────────────────────────────────────────────────
if [ "$NO_MISE" -eq 0 ]; then
  if ! command -v mise >/dev/null 2>&1; then
    cat >&2 <<'EOF'
mise is not installed. It pins python / node / go for this repo without touching
your system installs.

  brew install mise                       # macOS
  # then add the shell hook (once):  https://mise.jdx.dev/getting-started.html

Re-run this script after, or pass --no-mise to use PATH versions.
EOF
    exit 1
  fi
  say "mise: installing pinned toolchains (.mise.toml)"
  mise trust >/dev/null
  mise install
  PY="$(mise which python)"
  UV="$(mise which uv 2>/dev/null || true)"
else
  PY="$(command -v python3 || true)"
  UV="$(command -v uv || true)"
  [ -n "$PY" ] || { echo "no python3 on PATH" >&2; exit 1; }
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' \
    || warn "python $("$PY" -V) is < 3.10 — serving targets may misbehave (container is 3.10)"
  command -v go   >/dev/null || warn "no go on PATH — 'make check-local' Go steps will fail"
  command -v node >/dev/null || warn "no node on PATH — 'make check-local' web steps will fail"
fi

# ── 2. python venv + dev deps ───────────────────────────────────────────────
say "python venv: .venv  (serving/requirements-dev.txt)"
if [ -n "$UV" ]; then
  "$UV" venv --python "$PY" .venv >/dev/null
  "$UV" pip install --python .venv -q -r serving/requirements-dev.txt
else
  [ -d .venv ] || "$PY" -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r serving/requirements-dev.txt
fi

# ── 3. frontend deps ───────────────────────────────────────────────────────
say "frontend: npm ci"
( cd frontend && npm ci --silent )

# ── 4. per-worktree .env (feature worktrees only) ──────────────────────────
free_port() {  # first free TCP port at or above $1
  local p="$1"
  while lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; do p=$((p + 1)); done
  echo "$p"
}

if [ "$IS_CANONICAL" -eq 1 ]; then
  say ".env: skipped — the canonical checkout uses the compose defaults (5432/8000/5173, ./data)"
elif [ -f .env ]; then
  say ".env: exists — leaving it"
else
  DBP="$(free_port 5433)"; APIP="$(free_port $((8010 + (DBP - 5433))))"; WEBP="$(free_port $((5183 + (DBP - 5433))))"
  NAME="hh-$(basename "$ROOT" | sed 's/^hh-//')"
  cat > .env <<EOF
COMPOSE_PROJECT_NAME=$NAME
DB_PORT=$DBP
API_PORT=$APIP
WEB_PORT=$WEBP
VITE_API_URL=http://localhost:$APIP
HH_DATA_ROOT=$CANONICAL/data
DUCKDB_MEMORY_LIMIT=2GB
EOF
  say ".env: wrote $NAME  (db $DBP · api $APIP · web $WEBP · data ← $CANONICAL/data)"
fi

# ── 5. optional: local reference/CMS/summary store (GH #59 Part C) ────────────
# Read the big immutable stores from the shared corpus (ANTHEM_DIR/NPPES_DIR via
# the HH_DATA_ROOT mount), write the rebuilt ones into this worktree's
# data-local/ (already at /app/data-local via the repo mount). No override file.
if [ "$REBUILD_REF" -eq 1 ]; then
  say "data-local: seeding reference / cms / anthem-summary from $CANONICAL/data"
  for d in reference cms anthem/summary serving; do
    mkdir -p "data-local/$d"
    [ -d "$CANONICAL/data/$d" ] && cp -Rn "$CANONICAL/data/$d/." "data-local/$d/" 2>/dev/null || true
  done
  # append the split to .env if not already there
  if ! grep -q '^REFERENCE_DIR=' .env 2>/dev/null; then
    cat >> .env <<'EOF'

# --rebuild-reference (GH #59 Part C): read shared, write local.
ANTHEM_DIR=/app/data/anthem
NPPES_DIR=/app/data/nppes
REFERENCE_DIR=/app/data-local/reference
CMS_DIR=/app/data-local/cms
SUMMARY_DIR=/app/data-local/anthem/summary
SERVING_DIR=/app/data-local/serving
EOF
    say ".env: appended the sub-store split (rebuilds land in ./data-local/)"
  fi
fi

cat <<EOF

$(printf '\033[32m✓\033[0m') dev setup complete.

  make check-local     host-side gate — gofmt, vet, build, go test, pytest contract, vitest (no Docker)
$( [ "$IS_CANONICAL" -eq 1 ] || echo "  make stack-up        start this worktree's stack (own ports, from .env)" )
$( [ "$IS_CANONICAL" -eq 1 ] && echo "  make start           the canonical stack (Tailscale-visible)" )
EOF
