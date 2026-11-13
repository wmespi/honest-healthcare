#!/usr/bin/env bash
# Hermetic hospital-rates test with teardown.
# Runs the real extractor over the committed tall CSV, wide CSV and JSON fixtures in test mode,
# checks that a file it can't read keeps the hospital's earlier rates, verifies the rows with
# DuckDB, and removes what it wrote.
set -euo pipefail
cd "$(dirname "$0")/../.."

OUT=data-test/ingestion/hospital_rates
RAW=data-test/raw/hospital_rates
# Delete through the ingestion container - it wrote the parquet as root, so a host-side
# rm fails under a bind mount on Linux (CI). Falls back to a host rm if needed.
cleanup() { docker compose exec -T ingestion rm -rf "/app/$OUT" "/app/$RAW" 2>/dev/null || rm -rf "$OUT" "$RAW"; }
trap cleanup EXIT
cleanup

echo "→ unit tests (hospitalrates)"
docker compose exec -T ingestion go test ./hospitalrates/ -v 2>&1 | grep -E '^(--- |ok|FAIL)'

echo "→ extract each fixture (test output dir)"
for fixture in tall.csv:110010 wide.csv:110079 standard_charges.json:110029; do
  docker compose exec -T ingestion go run . hospital-rates -test \
    -file "hospitalrates/testdata/${fixture%%:*}" -ccn "${fixture#*:}"
done

echo "→ a file that isn't a price file fails and keeps the earlier rates"
if docker compose exec -T ingestion go run . hospital-rates -test -file hospitals.yaml -ccn 110010; then
  echo "FAIL: extracting a file that isn't a price file succeeded" >&2
  exit 1
fi

echo "→ verify parquet (via DuckDB)"
docker compose exec -T ingestion python3 -c "
import duckdb, glob, os
con = duckdb.connect()
src = \"read_parquet('/app/$OUT/*.parquet')\"
cols = [r[0] for r in con.execute(f'DESCRIBE SELECT * FROM {src}').fetchall()]
want = ['ccn', 'code_type', 'code', 'setting', 'billing_class', 'modifiers', 'payer', 'plan',
        'gross_charge', 'cash_price', 'negotiated_dollar', 'negotiated_percentage', 'methodology']
assert cols == want, f'columns {cols}'
rows = dict(con.execute(f'SELECT ccn, count(*) FROM {src} GROUP BY ccn').fetchall())
assert rows == {'110010': 10, '110079': 5, '110029': 5}, f'rows per hospital {rows}'
types = [r[0] for r in con.execute(f'SELECT DISTINCT code_type FROM {src} ORDER BY 1').fetchall()]
assert types == ['CPT', 'HCPCS', 'MS-DRG'], f'code types {types}'
drgs = [r[0] for r in con.execute(f\"SELECT DISTINCT code FROM {src} WHERE code_type = 'MS-DRG' ORDER BY 1\").fetchall()]
assert drgs == ['065', '470', '871'], f'MS-DRGs {drgs}'
payers = [r[0] for r in con.execute(f'SELECT DISTINCT payer FROM {src} WHERE payer IS NOT NULL ORDER BY 1').fetchall()]
assert payers == ['Anthem', 'Anthem Blue Cross', 'BCBS', 'Blue Cross', 'Blue Cross Blue Shield', 'BlueCross BlueShield'], f'payers {payers}'
charges, odd = con.execute(f'''SELECT count(*) FILTER (WHERE payer IS NULL),
  count(*) FILTER (WHERE payer IS NULL AND (plan IS NOT NULL OR negotiated_dollar IS NOT NULL
                                            OR negotiated_percentage IS NOT NULL OR methodology IS NOT NULL))
  FROM {src}''').fetchone()
assert charges == 6 and odd == 0, f'{charges} charge-only rows, {odd} with a payer field'
left = glob.glob('/app/$OUT/*.part')
assert not left, f'left behind: {left}'
assert not os.path.exists('/app/$RAW'), 'the download folder was left behind'
print(f'   {sum(rows.values())} rows from 3 hospitals, {charges} charge-only, payers kept: {len(payers)}')
"
echo "PASS"
