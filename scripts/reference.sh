#!/usr/bin/env bash
# make reference: build the reference datasets, in dependency order, skipping any whose
# output already exists (FORCE=1 rebuilds), except geocode, which has no single output
# and looks up only new addresses. STEP=<name> runs just that one, always.
# ARGS="--year 2025" passes extra flags through to the STEP's builder.
#
#   make reference                      # everything missing
#   make reference STEP=mpfs ARGS="--year 2025"
#   make reference FORCE=1
#
# geocode always runs too: it asks each geocoder only about the addresses it hasn't before.
set -euo pipefail
cd "$(dirname "$0")/.."

STEP="${STEP:-}"
FORCE="${FORCE:-}"
ARGS="${ARGS:-}"

# name | output (inside the ingestion container) | command
# A step that writes several files names the last one it writes.
STEPS=(
  "nppes|/app/data/ingestion/nppes/ga_locations.parquet|etl"
  "taxonomy-labels|/app/data/ingestion/reference/nucc_taxonomy.parquet|ingestion.reference.taxonomy_labels"
  "cms-utilization|/app/data/ingestion/cms/ga_provider_service.parquet|ingestion.reference.cms_utilization"
  "doctors-clinicians|/app/data/ingestion/reference/dac_locations_ga.parquet|ingestion.reference.doctors_clinicians"
  "mpfs|/app/data/ingestion/reference/mpfs_ga.parquet|ingestion.reference.mpfs"
  "opps|/app/data/ingestion/reference/opps.parquet|ingestion.reference.opps"
  "hospitals|/app/data/ingestion/reference/hospitals_ga.parquet|ingestion.reference.hospitals"
  "geocode||ingestion.reference.geocode"
  "mips|/app/data/ingestion/reference/mips_ga.parquet|ingestion.reference.mips"
  "zip-centroids|/app/data/ingestion/reference/zip_centroids.parquet|ingestion.reference.zip_centroids"
  "exchange-plans|/app/data/ingestion/reference/exchange_plans_ga.parquet|ingestion.reference.exchange_plans"
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
     docker compose exec -T ingestion test -e "$out" 2>/dev/null; then
    echo "→ $name: exists ($out) — skipping (FORCE=1 to rebuild)"
    continue
  fi
  echo "→ $name"
  if [ "$cmd" = etl ]; then
    # shellcheck disable=SC2086
    docker compose exec ingestion go run . nppes $ARGS
  else
    # shellcheck disable=SC2086
    docker compose exec -T -w /app ingestion python3 -m "$cmd" --data-dir /app/data $ARGS
  fi
done
