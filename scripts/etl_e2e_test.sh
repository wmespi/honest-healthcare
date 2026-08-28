#!/usr/bin/env bash
# Hermetic ETL end-to-end test with full teardown.
#
# Runs the real Phase-2 parse against a committed *.json.gz fixture in the
# isolated `test` schema, writing Parquet under data-test/. Verifies row counts,
# the network_name column, and the coverage_log row. Cleans up everything on
# exit — `test.*` tables truncated, data-test/anthem removed — so nothing
# accumulates on disk.
set -euo pipefail

cd "$(dirname "$0")/.."

FIXTURE="testdata/fixtures/synthetic.json.gz"
PSQL=(docker compose exec -T db psql -U postgres -d honest_healthcare -v ON_ERROR_STOP=1 -qtA)

cleanup() {
  echo "→ teardown"
  "${PSQL[@]}" -c "TRUNCATE test.index_files, test.billing_codes, test.coverage_log RESTART IDENTITY;" >/dev/null 2>&1 || true
  rm -rf data-test/anthem
}
trap cleanup EXIT

echo "→ unit tests"
docker compose exec -T etl_go go test ./...

echo "→ seed test.index_files"
cleanup  # start from a clean slate
FILE_ID=$("${PSQL[@]}" -c \
  "INSERT INTO test.index_files (location, market_types, hios_issuer_ids, plan_states, status)
   VALUES ('fixture://synthetic', ARRAY['individual','group'], ARRAY['45334'], ARRAY['GA'], 'pending')
   RETURNING id;")
echo "   file id = $FILE_ID"

echo "→ parse fixture (test isolation)"
docker compose exec -T etl_go go run . -parse -test -file-ids "$FILE_ID" -fixture "$FIXTURE"

echo "→ verify parquet output (via backend duckdb)"
RATE_ROWS=$(docker compose exec -T backend python3 -c "
import duckdb, glob
files = glob.glob('/app/data-test/anthem/rates/*.parquet')
assert files, 'no rates parquet written'
con = duckdb.connect()
cols = [c[0] for c in con.execute(f\"DESCRIBE SELECT * FROM read_parquet('{files[0]}')\").fetchall()]
assert 'network_name' in cols, f'network_name column missing: {cols}'
n = con.execute(f\"SELECT count(*) FROM read_parquet('/app/data-test/anthem/rates/*.parquet')\").fetchone()[0]
ga = con.execute(f\"\"\"SELECT count(*) FROM read_parquet('/app/data-test/anthem/rates/*.parquet')
                     WHERE network_name = 'GA Blue Value HIX Individual Network'\"\"\").fetchone()[0]
print(f'{n} {ga}')
")
read -r N GA <<< "$RATE_ROWS"
echo "   rate rows=$N  attributed to GA Blue Value Individual=$GA"
# 5, not 6: the default 'GA *' network allowlist drops the fixture's one
# provider group that has no network_name (and its single rate row).
[ "$N" = "5" ] || { echo "FAIL: expected 5 rate rows (GA-network filtered), got $N"; exit 1; }
[ "$GA" -ge 1 ] || { echo "FAIL: no rate rows attributed to the target network"; exit 1; }

echo "→ verify status + coverage_log"
STATUS=$("${PSQL[@]}" -c "SELECT status FROM test.index_files WHERE id = $FILE_ID;")
[ "$STATUS" = "completed" ] || { echo "FAIL: status=$STATUS, want completed"; exit 1; }

COV=$("${PSQL[@]}" -c "SELECT n_rate_rows || '/' || n_new_billing_codes || '/' || array_to_string(network_names, ',') FROM test.coverage_log WHERE file_id = $FILE_ID;")
echo "   coverage_log (rates/newcodes/networks): $COV"
[ -n "$COV" ] || { echo "FAIL: no coverage_log row"; exit 1; }

echo "PASS"
