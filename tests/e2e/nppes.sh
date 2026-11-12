#!/usr/bin/env bash
# Hermetic NPPES GA-extraction test with teardown.
# Runs the real extractor over the committed CSV fixtures, verifies the GA filter,
# the hospital/clinic classification and the secondary locations, and removes the output.
set -euo pipefail
cd "$(dirname "$0")/../.."

OUT=data-test/ingestion/nppes/ga_providers.parquet
LOCATIONS=data-test/ingestion/nppes/ga_locations.parquet
# Delete through the ingestion container - it wrote the parquet as root, so a host-side
# rm fails under a bind mount on Linux (CI). Falls back to a host rm if needed.
cleanup() { docker compose exec -T ingestion rm -rf /app/data-test/ingestion/nppes 2>/dev/null || rm -rf data-test/ingestion/nppes; }
trap cleanup EXIT
cleanup

echo "→ unit tests (nppes)"
docker compose exec -T ingestion go test ./nppes/ -run 'NPPES|ClassifyTaxonomy' -v 2>&1 | tail -8

echo "→ extract GA subset from fixture (test output dir)"
docker compose exec -T ingestion go run . nppes -test -file nppes/testdata/nppes_sample.csv \
  -locations-file nppes/testdata/pl_sample.csv

echo "→ verify parquet (via DuckDB)"
docker compose exec -T ingestion python3 -c "
import duckdb
con = duckdb.connect()
n, h, c = con.execute('''
  SELECT count(*),
         count(*) FILTER (WHERE is_hospital),
         count(*) FILTER (WHERE is_clinic)
  FROM read_parquet('/app/$OUT')
''').fetchone()
assert n == 12, f'expected 12 GA rows, got {n}'
assert h == 4, f'expected 4 hospitals, got {h}'
assert c == 2, f'expected 2 clinics, got {c}'
bad = con.execute('''SELECT count(*) FROM read_parquet('/app/$OUT') WHERE state <> 'GA' ''').fetchone()[0]
assert bad == 0, f'{bad} non-GA rows leaked'
locations, elsewhere = con.execute('''
  SELECT count(*), count(*) FILTER (WHERE state <> 'GA') FROM read_parquet('/app/$LOCATIONS')
''').fetchone()
assert locations == 3, f'expected 3 GA secondary locations, got {locations}'
assert elsewhere == 0, f'{elsewhere} non-GA secondary locations leaked'
print(f'   {n} GA providers  ({h} hospitals, {c} clinics), {locations} secondary locations')
"
echo "PASS"
