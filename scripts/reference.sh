#!/usr/bin/env bash
# Phase 3 — build the reference datasets, in dependency order, skipping any whose
# output already exists (FORCE=1 rebuilds). STEP=<name> runs just that one, always.
# ARGS="--year 2025" passes extra flags through to the STEP's builder.
#
#   make reference                      # everything missing
#   make reference STEP=mpfs ARGS="--year 2025"
#   make reference FORCE=1
#
# code-labels reads the parsed codes under data/anthem/codes, so it always re-runs.
set -euo pipefail
cd "$(dirname "$0")/.."

STEP="${STEP:-}"
FORCE="${FORCE:-}"
ARGS="${ARGS:-}"

# name | output (inside the serving container) | command
STEPS=(
  "nppes|/app/data/nppes/ga_providers.parquet|etl"
  "taxonomy-labels|/app/data/reference/nucc_taxonomy.parquet|reference.taxonomy_labels"
  "code-labels||reference.code_labels"
  "cms-utilization|/app/data/cms/ga_provider_service.parquet|reference.cms_utilization"
  "doctors-clinicians|/app/data/reference/dac_ga.parquet|reference.doctors_clinicians"
  "specialty-profiles|/app/data/reference/specialty_procedure_profiles.parquet|reference.specialty_profiles"
  "mpfs|/app/data/reference/mpfs_ga.parquet|reference.mpfs"
  "geocode|/app/data/reference/pcp_geocode.parquet|reference.geocode"
)

if [ -n "$STEP" ]; then
  found=0
  for s in "${STEPS[@]}"; do [ "${s%%|*}" = "$STEP" ] && found=1; done
  if [ "$found" = 0 ]; then
    echo "unknown STEP=$STEP — one of: $(for s in "${STEPS[@]}"; do printf '%s ' "${s%%|*}"; done)" >&2
    exit 1
  fi
fi

for s in "${STEPS[@]}"; do
  IFS='|' read -r name out cmd <<<"$s"
  [ -n "$STEP" ] && [ "$STEP" != "$name" ] && continue
  if [ -z "$STEP" ] && [ "$FORCE" != 1 ] && [ -n "$out" ] &&
     docker compose exec -T serving test -e "$out" 2>/dev/null; then
    echo "→ $name: exists ($out) — skipping (FORCE=1 to rebuild)"
    continue
  fi
  echo "→ $name"
  if [ "$cmd" = etl ]; then
    # shellcheck disable=SC2086
    docker compose exec etl go run . nppes $ARGS
  else
    # shellcheck disable=SC2086
    docker compose exec -T -w /app serving python3 -m "$cmd" --data-dir /app/data $ARGS
  fi
done
