#!/usr/bin/env bash
# Hermetic discover end-to-end test with full teardown.
#
# Runs the real `etl discover` in test mode against two small monthly indexes
# served over HTTP, and checks that each run writes that month's lists: a file
# keeps its ID when only its signed location changes, a file the index drops is
# gone, the plan links are this month's only and each names a listed file, the
# same index always writes the same bytes, and the downloaded index is deleted
# after a run unless it's kept.
set -euo pipefail

cd "$(dirname "$0")/../.."
source tests/e2e/lib.sh

PORT=8766
HOST="http://127.0.0.1:$PORT"

cleanup() {
  echo "→ teardown"
  serve_stop "$PORT"
  # Delete through the ingestion container - it wrote these files as root, so a host-side
  # rm fails under a bind mount on Linux (CI). Falls back to a host rm if needed.
  docker compose exec -T ingestion rm -rf /app/data-test/raw/anthem /app/data-test/raw/e2e_discover 2>/dev/null \
    || rm -rf data-test/raw/anthem data-test/raw/e2e_discover
}
trap cleanup EXIT
cleanup
store_mark

echo "→ write two months of index"
# October lists GA_KEEP unsigned. November signs it, links it to a different
# plan, drops GA_GONE, and adds GA_NEW.
docker compose exec -T ingestion python3 - <<'EOF'
import gzip, json, os
os.makedirs("/app/data-test/raw/e2e_discover", exist_ok=True)
def plan(plan_id):
    return {"plan_name": "TEST PLAN " + plan_id, "plan_id_type": "HIOS", "plan_id": plan_id, "plan_market_type": "individual"}
def files(*locations):
    return [{"description": "test", "location": l} for l in locations]
months = {
    "2026-10-01_index.json.gz": [
        {"reporting_plans": [plan("45334GA0020008")],
         "in_network_files": files("http://x/GA_KEEP.json.gz", "http://x/SHARD.json.gz?Expires=10")},
        {"reporting_plans": [plan("45334GA0020008")],
         "in_network_files": files("http://x/GA_GONE.json.gz")},
    ],
    "2026-11-01_index.json.gz": [
        {"reporting_plans": [plan("45334GA0020009")],
         "in_network_files": files("http://x/GA_KEEP.json.gz?Expires=11", "http://x/SHARD.json.gz?Expires=11")},
        {"reporting_plans": [plan("45334GA0020008")],
         "in_network_files": files("http://x/GA_NEW.json.gz")},
    ],
}
for name, structures in months.items():
    with gzip.open(f"/app/data-test/raw/e2e_discover/{name}", "wt") as f:
        json.dump({"reporting_entity_name": "Test", "reporting_entity_type": "test",
                   "reporting_structure": structures}, f)
EOF
serve_start e2e_discover "$PORT"

discover() {  # discover <index file> [more flags]
  local index=$1
  shift
  docker compose exec -T ingestion go run . discover -test -index-url "$HOST/$index" "$@"
}
has_index() {  # whether the downloaded index is still on disk
  docker compose exec -T ingestion test -f /app/data-test/raw/anthem/index_cache.json.gz
}
query() {  # query <SQL over the views files and links>: its one value
  docker compose exec -T ingestion python3 -c "
import duckdb
con = duckdb.connect()
con.execute(\"CREATE VIEW files AS FROM '/app/data-test/raw/anthem/index_files.parquet'\")
con.execute(\"CREATE VIEW links AS FROM '/app/data-test/raw/anthem/index_file_plans.parquet'\")
v = con.execute(\"\"\"$1\"\"\").fetchone()[0]
print('' if v is None else v)"
}
id_of() {  # id_of <file name>
  query "SELECT coalesce(max(file_id)::VARCHAR, 'none') FROM files WHERE address = 'http://x/$1'"
}
links() {  # every (file, plan) link, as name:plan
  query "SELECT string_agg(f.name || ':' || l.plan_id, ' ' ORDER BY f.name, l.plan_id) FROM links l JOIN files f USING (file_id)"
}
checksum() {
  docker compose exec -T ingestion sh -c 'cat /app/data-test/raw/anthem/index_files.parquet /app/data-test/raw/anthem/index_file_plans.parquet | md5sum'
}

echo "→ discover October"
discover 2026-10-01_index.json.gz
! has_index || { echo "FAIL: discover kept the downloaded index"; exit 1; }
KEEP=$(id_of GA_KEEP.json.gz)
SHARD=$(id_of SHARD.json.gz)
[ "$(id_of GA_GONE.json.gz)" != "none" ] || { echo "FAIL: October doesn't list GA_GONE"; exit 1; }
WANT="GA_GONE.json.gz:45334GA0020008 GA_KEEP.json.gz:45334GA0020008 SHARD.json.gz:45334GA0020008"
[ "$(links)" = "$WANT" ] || { echo "FAIL: links are $(links), not October's ($WANT)"; exit 1; }

echo "→ discover November"
discover 2026-11-01_index.json.gz
[ "$(id_of GA_KEEP.json.gz)" = "$KEEP" ] || { echo "FAIL: GA_KEEP changed ID ($KEEP → $(id_of GA_KEEP.json.gz))"; exit 1; }
[ "$(id_of SHARD.json.gz)" = "$SHARD" ] || { echo "FAIL: SHARD changed ID when only its signature did"; exit 1; }
LOCATION=$(query "SELECT location FROM files WHERE file_id = $SHARD")
[ "$LOCATION" = "http://x/SHARD.json.gz?Expires=11" ] || { echo "FAIL: SHARD kept last month's location ($LOCATION)"; exit 1; }
[ "$(id_of GA_GONE.json.gz)" = "none" ] || { echo "FAIL: November still lists GA_GONE"; exit 1; }
[ "$(id_of GA_NEW.json.gz)" != "none" ] || { echo "FAIL: November doesn't list GA_NEW"; exit 1; }
LINKS=$(links)
echo "   links: $LINKS"
WANT="GA_KEEP.json.gz:45334GA0020009 GA_NEW.json.gz:45334GA0020008 SHARD.json.gz:45334GA0020009"
[ "$LINKS" = "$WANT" ] || { echo "FAIL: links are not November's ($WANT)"; exit 1; }
ORPHANS=$(query "SELECT count(*) FROM links ANTI JOIN files USING (file_id)")
[ "$ORPHANS" = "0" ] || { echo "FAIL: $ORPHANS link(s) name a file the list doesn't have"; exit 1; }

echo "→ the same index writes the same bytes, and a kept index is read again"
SUM=$(checksum)
discover 2026-11-01_index.json.gz -keep-index
has_index || { echo "FAIL: -keep-index didn't keep the downloaded index"; exit 1; }
[ "$(checksum)" = "$SUM" ] || { echo "FAIL: a second run over the same index wrote different bytes"; exit 1; }
serve_stop "$PORT"  # the next run has to read the kept copy
OUT=$(discover 2026-11-01_index.json.gz 2>&1)
echo "$OUT" | grep -q "Cache hit" || { echo "FAIL: discover didn't read the kept index:"; echo "$OUT" | tail -3; exit 1; }
[ "$(checksum)" = "$SUM" ] || { echo "FAIL: the kept index wrote different bytes"; exit 1; }
! has_index || { echo "FAIL: discover kept the index without -keep-index"; exit 1; }

store_untouched

echo "PASS"
