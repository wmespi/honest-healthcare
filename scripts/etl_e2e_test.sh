#!/usr/bin/env bash
# Hermetic ETL end-to-end test with full teardown.
#
# Runs the real Phase-2 parse against a committed *.json.gz fixture in the
# isolated `test` schema, writing Parquet under data-test/. Verifies the price /
# group_set row counts, the network partitioning, and the coverage_log row.
# Cleans up everything on exit — `test.*` tables truncated, data-test/anthem
# removed — so nothing accumulates on disk.
set -euo pipefail

cd "$(dirname "$0")/.."

FIXTURE="extraction/testdata/fixtures/synthetic.json.gz"
PSQL=(docker compose exec -T db psql -U postgres -d honest_healthcare -v ON_ERROR_STOP=1 -qtA)

cleanup() {
  echo "→ teardown"
  "${PSQL[@]}" -c "TRUNCATE test.index_file_plans, test.index_files, test.billing_codes, test.coverage_log RESTART IDENTITY;" >/dev/null 2>&1 || true
  # Delete through the etl container — it wrote these files as root, so a host-side
  # rm fails under a bind mount on Linux (CI). Falls back to a host rm if needed.
  docker compose exec -T etl rm -rf /app/data-test/anthem /app/data-test/reference/exchange_plans_ga.parquet \
    /app/data-test/e2e_targets.yaml 2>/dev/null || rm -rf data-test/anthem data-test/e2e_targets.yaml
}
trap cleanup EXIT

echo "→ unit tests"
docker compose exec -T etl go test ./...

echo "→ seed test.index_files"
cleanup  # start from a clean slate
FILE_ID=$("${PSQL[@]}" -c \
  "INSERT INTO test.index_files (location, market_types, hios_issuer_ids, plan_states, status)
   VALUES ('fixture://synthetic', ARRAY['individual','group'], ARRAY['45334'], ARRAY['GA'], 'pending')
   RETURNING id;")
echo "   file id = $FILE_ID"

echo "→ parse fixture (test isolation)"
docker compose exec -T etl go run . parse -test -file-ids "$FILE_ID" -fixture "$FIXTURE"

echo "→ verify parquet output (via serving duckdb)"
RATE_ROWS=$(docker compose exec -T serving python3 -c "
import duckdb, glob
prices = glob.glob('/app/data-test/anthem/prices/**/*.parquet', recursive=True)
gsets  = glob.glob('/app/data-test/anthem/group_sets/*.parquet')
assert prices, 'no prices parquet written'
assert gsets, 'no group_sets parquet written'
assert any('/net=' in f for f in prices), f'prices not partitioned by net: {prices}'
con = duckdb.connect()
P = \"read_parquet('/app/data-test/anthem/prices/**/*.parquet', hive_partitioning=1)\"
G = \"read_parquet('/app/data-test/anthem/group_sets/*.parquet')\"
pcols = [c[0] for c in con.execute(f'DESCRIBE SELECT * FROM {P}').fetchall()]
assert {'network_name', 'net', 'file_id', 'group_set_id'} <= set(pcols), f'price cols: {pcols}'
# every price row's group_set_id must resolve to >=1 membership edge
orphans = con.execute(f'''SELECT count(*) FROM {P} p
  WHERE NOT EXISTS (SELECT 1 FROM {G} m
    WHERE m.file_id = p.file_id AND m.group_set_id = p.group_set_id)''').fetchone()[0]
assert orphans == 0, f'{orphans} price rows with no group_set members'
n  = con.execute(f'SELECT count(*) FROM {P}').fetchone()[0]
ga = con.execute(f\"SELECT count(*) FROM {P} WHERE net = 'ga-blue-value-hix-individual-network'\").fetchone()[0]
print(f'{n} {ga}')
")
read -r N GA <<< "$RATE_ROWS"
echo "   price rows=$N  attributed to GA Blue Value Individual=$GA"
[ "$N" -ge 1 ] || { echo "FAIL: no price rows written"; exit 1; }
[ "$GA" -ge 1 ] || { echo "FAIL: no price rows attributed to the target network"; exit 1; }

echo "→ verify status + coverage_log"
STATUS=$("${PSQL[@]}" -c "SELECT status FROM test.index_files WHERE id = $FILE_ID;")
[ "$STATUS" = "completed" ] || { echo "FAIL: status=$STATUS, want completed"; exit 1; }

COV=$("${PSQL[@]}" -c "SELECT n_rate_rows || '/' || n_new_billing_codes || '/' || array_to_string(network_names, ',') FROM test.coverage_log WHERE file_id = $FILE_ID;")
echo "   coverage_log (rates/newcodes/networks): $COV"
[ -n "$COV" ] || { echo "FAIL: no coverage_log row"; exit 1; }

echo "→ parse downloads only the files a targets file names"
# Both files are pending and the index links both to a Blue Value plan, the way
# it links Blue Value to out-of-state shards. Only the named one is tried (its
# address refuses, so it fails fast); the other is never touched.
docker compose exec -T serving python3 -c "
import os, duckdb
os.makedirs('/app/data-test/reference', exist_ok=True)
duckdb.connect().execute(\"COPY (SELECT '45334GA0020008' AS plan_id, '45334-GAN001' AS network_id) TO '/app/data-test/reference/exchange_plans_ga.parquet' (FORMAT parquet)\")
"
NAMED=$("${PSQL[@]}" -c "INSERT INTO test.index_files (location) VALUES ('http://127.0.0.1:9/GA_NAMED.json.gz') RETURNING id;")
SHARD=$("${PSQL[@]}" -c "INSERT INTO test.index_files (location) VALUES ('http://127.0.0.1:9/2026-10_301_71B0_in-network-rates_01_of_02.json.gz?Expires=1') RETURNING id;")
"${PSQL[@]}" -c "INSERT INTO test.index_file_plans (file_id, plan_id, plan_id_type)
                 VALUES ($NAMED, '45334GA0020008', 'HIOS'), ($SHARD, '45334GA0020008', 'HIOS');"
printf 'targets:\n  - name: Blue Value\n    exchange_networks: ["45334-GAN001"]\n    files: ["GA_NAMED.json.gz"]\n' > data-test/e2e_targets.yaml
docker compose exec -T etl go run . parse -test -targets ../data-test/e2e_targets.yaml || true
NAMED_STATUS=$("${PSQL[@]}" -c "SELECT status FROM test.index_files WHERE id = $NAMED;")
SHARD_STATUS=$("${PSQL[@]}" -c "SELECT status FROM test.index_files WHERE id = $SHARD;")
echo "   named file: $NAMED_STATUS  unnamed file: $SHARD_STATUS"
[ "$NAMED_STATUS" = "failed" ] || { echo "FAIL: the named file wasn't tried (status=$NAMED_STATUS)"; exit 1; }
[ "$SHARD_STATUS" = "pending" ] || { echo "FAIL: parse touched a file the targets don't name (status=$SHARD_STATUS)"; exit 1; }

echo "PASS"
