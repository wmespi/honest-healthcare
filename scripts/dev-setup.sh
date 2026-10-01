#!/usr/bin/env bash
# Set up host toolchains + per-worktree Docker config for parallel development
# (GH #59). Idempotent — safe to re-run.
#
#   scripts/dev-setup.sh [--no-mise]
#
#   --no-mise             use whatever python3 / node / go are on PATH instead of
#                         installing pinned versions via mise
#
# The container-only quickstart (README.md) needs none of this.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

NO_MISE=0
for a in "$@"; do
  case "$a" in
    --no-mise) NO_MISE=1 ;;
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
  command -v go   >/dev/null || warn "no go on PATH — 'make check LOCAL=1' Go steps will fail"
  command -v node >/dev/null || warn "no node on PATH — 'make test LOCAL=1' web steps will fail"
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

cat <<EOF

$(printf '\033[32m✓\033[0m') dev setup complete.

  make test LOCAL=1    host-side gate — gofmt, vet, build, go test, pytest, vitest (no Docker)
  make start           this checkout's stack$( [ "$IS_CANONICAL" -eq 1 ] || echo " (own ports, from .env)" )
EOF
