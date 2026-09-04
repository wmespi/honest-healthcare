#!/usr/bin/env bash
# Reclaim disk in this worktree (and, with DOCKER=1, the shared Docker store).
#   make clean            # regenerable local artifacts in this worktree
#   make clean DOCKER=1    # + docker build cache / dangling images / unused volumes
#
# Never touches: source, .git, data/ (the parsed store), the running stack's
# postgres_data volume (it's in-use, so `docker volume prune` skips it).
set -euo pipefail
cd "$(dirname "$0")/.."

before=$(df -k /System/Volumes/Data 2>/dev/null | awk 'NR==2{print $4}' || echo 0)

say() { printf '\033[36m•\033[0m %s\n' "$*"; }

# ── stray DuckDB spill — the thing that leaked 176 GB (Aug 29) ────────────────
for d in .tmp .duckdb_spill; do
  [ -d "$d" ] && { say "rm $d/  ($(du -sh "$d" | cut -f1))"; rm -rf "$d"; }
done

# ── regenerable test / cache dirs ────────────────────────────────────────────
for d in data-test .pytest_cache .ruff_cache; do
  [ -d "$d" ] && { say "rm $d/  ($(du -sh "$d" | cut -f1))"; rm -rf "$d"; }
done
find . -type d -name __pycache__ -not -path './.venv/*' -not -path './frontend/node_modules/*' \
  -exec rm -rf {} + 2>/dev/null || true
say "cleared __pycache__"

# data-local/ is a --rebuild-reference cache — keep unless asked
if [ -d data-local ] && [ "${DATA_LOCAL:-}" = "1" ]; then
  say "rm data-local/  ($(du -sh data-local | cut -f1))"; rm -rf data-local
elif [ -d data-local ]; then
  say "data-local/ kept ($(du -sh data-local | cut -f1)) — DATA_LOCAL=1 to drop it"
fi

# reference-builder download caches (data*/{cms,reference}/.cache) — the raw
# CMS/NPPES source files. The built .parquet doesn't need them; a rebuild
# re-downloads. Opt-in (CACHE=1) since a --rebuild-reference worktree wants them.
if [ "${CACHE:-}" = "1" ]; then
  for cd in data*/*/.cache data*/.cache; do
    [ -d "$cd" ] && { say "rm $cd/  ($(du -sh "$cd" | cut -f1))"; rm -rf "$cd"; }
  done
else
  found=$(du -shc data*/*/.cache 2>/dev/null | tail -1 | cut -f1) || found=""
  # a bare `A && B && C` here would trip `set -e` when the middle test is
  # false (no caches found) — an `if` makes "nothing to report" not an error.
  if [ -n "$found" ] && [ "$found" != "0B" ]; then
    say "reference download caches kept ($found) — CACHE=1 to drop them"
  fi
fi

git worktree prune
say "git worktree prune"

# ── Docker (opt-in — shared across every worktree AND other projects) ─────────
#   DOCKER=1    build cache (all), dangling images, unused volumes  — safe
#   DOCKER=all  + every image not backing a running container      — also drops
#               other projects' images; they rebuild on next use
case "${DOCKER:-}" in
  1|all)
    if docker info >/dev/null 2>&1; then
      say "docker builder prune -a"; docker builder prune -af | tail -1
      if [ "$DOCKER" = "all" ]; then
        say "docker image prune -a (every image not backing a running container)"
        docker image prune -af | tail -1
      else
        say "docker image prune (dangling only — DOCKER=all for the rest)"
        docker image prune -f | tail -1
      fi
      say "docker volume prune (unused only — the running stack's is safe)"
      docker volume prune -f | tail -1
    else
      say "docker not running — skipped"
    fi ;;
esac

after=$(df -k /System/Volumes/Data 2>/dev/null | awk 'NR==2{print $4}' || echo 0)
freed=$(( (after - before) / 1024 ))
printf '\n\033[32m✓\033[0m reclaimed ~%s MB (host volume free: %s → %s MB)\n' \
  "$freed" "$((before/1024))" "$((after/1024))"
