#!/usr/bin/env bash
# Hermetic NPPES GA-extraction test with teardown.
# Runs the real extractor over the committed ~14-row CSV fixture, verifies the
# GA filter + hospital/clinic classification, and removes the output.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=data-test/nppes/ga_providers.parquet
cleanup() { rm -rf data-test/nppes; }
trap cleanup EXIT
cleanup

echo "→ unit tests (nppes)"
docker compose exec -T etl go test ./nppes/ -run 'NPPES|ClassifyTaxonomy' -v 2>&1 | tail -8

echo "→ extract GA subset from fixture (test output dir)"
docker compose exec -T etl go run . nppes -test -file nppes/testdata/nppes_sample.csv

echo "→ verify parquet (via serving duckdb)"
docker compose exec -T serving python3 -c "
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
print(f'   {n} GA providers  ({h} hospitals, {c} clinics)')
"
echo "PASS"
