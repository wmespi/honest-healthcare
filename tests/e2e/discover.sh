#!/usr/bin/env bash
# Hermetic discover end-to-end test with full teardown.
#
# Runs the real `etl discover` in the isolated `test` schema against two small
# monthly indexes served over HTTP, and checks that the second month rebuilds the
# queue: a file keeps its id when only its signed location changes, a file the
# index drops is removed unless it was parsed, the plan links are this month's
# only, a limited run removes nothing, and a file left 'processing' stops it.
set -euo pipefail

cd "$(dirname "$0")/../.."
source tests/e2e/lib.sh

PORT=8766
PSQL=(docker compose exec -T db psql -U postgres -d honest_healthcare -v ON_ERROR_STOP=1 -qtA)
HOST="http://serving:$PORT"

cleanup() {
  echo "→ teardown"
  serve_stop "$PORT"
  "${PSQL[@]}" -c "TRUNCATE test.index_file_plans, test.index_files RESTART IDENTITY;" >/dev/null 2>&1 || true
  # Delete through the etl container — it wrote these files as root, so a host-side
  # rm fails under a bind mount on Linux (CI). Falls back to a host rm if needed.
  docker compose exec -T etl rm -rf /app/data-test/anthem /app/data-test/e2e_discover 2>/dev/null \
    || rm -rf data-test/anthem data-test/e2e_discover
}
trap cleanup EXIT
cleanup
PUBLIC_IDS=$(public_ids)

echo "→ write two months of index"
# October lists GA_KEEP unsigned. November signs it, links it to a different
# plan, drops GA_PARSED and GA_GONE, and adds GA_NEW.
docker compose exec -T serving python3 - <<'EOF'
import gzip, json, os
os.makedirs("/app/data-test/e2e_discover", exist_ok=True)
def plan(plan_id):
    return {"plan_name": "TEST PLAN " + plan_id, "plan_id_type": "HIOS", "plan_id": plan_id, "plan_market_type": "individual"}
def files(*locations):
    return [{"description": "test", "location": l} for l in locations]
months = {
    "2026-10-01_index.json.gz": [
        {"reporting_plans": [plan("45334GA0020008")],
         "in_network_files": files("http://x/GA_KEEP.json.gz", "http://x/SHARD.json.gz?Expires=10")},
        {"reporting_plans": [plan("45334GA0020008")],
         "in_network_files": files("http://x/GA_PARSED.json.gz", "http://x/GA_GONE.json.gz")},
    ],
    "2026-11-01_index.json.gz": [
        {"reporting_plans": [plan("45334GA0020009")],
         "in_network_files": files("http://x/GA_KEEP.json.gz?Expires=11", "http://x/SHARD.json.gz?Expires=11")},
        {"reporting_plans": [plan("45334GA0020008")],
         "in_network_files": files("http://x/GA_NEW.json.gz")},
    ],
}
for name, structures in months.items():
    with gzip.open(f"/app/data-test/e2e_discover/{name}", "wt") as f:
        json.dump({"reporting_entity_name": "Test", "reporting_entity_type": "test",
                   "reporting_structure": structures}, f)
EOF
serve_start e2e_discover "$PORT"

discover() {  # discover <index file> [more flags]
  local index=$1
  shift
  docker compose exec -T etl go run . discover -test -index-url "$HOST/$index" "$@"
}
id_of() {  # id_of <file name>
  "${PSQL[@]}" -c "SELECT COALESCE(max(id)::text, 'none') FROM test.index_files WHERE address = 'http://x/$1';"
}
links() {  # every (file, plan) link, as name:plan
  "${PSQL[@]}" -c "SELECT string_agg(link, ' ' ORDER BY link) FROM (
                     SELECT regexp_replace(f.address, '^.*/', '') || ':' || p.plan_id AS link
                     FROM test.index_file_plans p JOIN test.index_files f ON f.id = p.file_id) l;"
}

echo "→ discover October"
discover 2026-10-01_index.json.gz
KEEP=$(id_of GA_KEEP.json.gz)
SHARD=$(id_of SHARD.json.gz)
PARSED=$(id_of GA_PARSED.json.gz)
"${PSQL[@]}" -c "UPDATE test.index_files SET status = 'completed' WHERE id = $PARSED;"

echo "→ discover November"
discover 2026-11-01_index.json.gz
[ "$(id_of GA_KEEP.json.gz)" = "$KEEP" ] || { echo "FAIL: GA_KEEP changed id ($KEEP → $(id_of GA_KEEP.json.gz))"; exit 1; }
[ "$(id_of SHARD.json.gz)" = "$SHARD" ] || { echo "FAIL: SHARD changed id when only its signature did"; exit 1; }
LOCATION=$("${PSQL[@]}" -c "SELECT location FROM test.index_files WHERE id = $SHARD;")
[ "$LOCATION" = "http://x/SHARD.json.gz?Expires=11" ] || { echo "FAIL: SHARD kept last month's location ($LOCATION)"; exit 1; }
[ "$(id_of GA_PARSED.json.gz)" = "$PARSED" ] || { echo "FAIL: discover removed a parsed file"; exit 1; }
[ "$(id_of GA_GONE.json.gz)" = "none" ] || { echo "FAIL: discover kept a file the index dropped"; exit 1; }
[ "$(id_of GA_NEW.json.gz)" != "none" ] || { echo "FAIL: discover didn't add GA_NEW"; exit 1; }
LINKS=$(links)
echo "   links: $LINKS"
WANT="GA_KEEP.json.gz:45334GA0020009 GA_NEW.json.gz:45334GA0020008 SHARD.json.gz:45334GA0020009"
[ "$LINKS" = "$WANT" ] || { echo "FAIL: links are not November's ($WANT)"; exit 1; }

echo "→ a limited read removes nothing"
discover 2026-10-01_index.json.gz -limit 1
[ "$(id_of GA_NEW.json.gz)" != "none" ] || { echo "FAIL: a limited read removed GA_NEW"; exit 1; }

echo "→ a file left processing stops discover"
"${PSQL[@]}" -c "UPDATE test.index_files SET status = 'processing' WHERE id = $KEEP;"
if OUT=$(discover 2026-11-01_index.json.gz 2>&1); then
  echo "FAIL: discover rebuilt the queue under a file being parsed"; exit 1
fi
echo "$OUT" | grep -q "'processing'" || { echo "FAIL: discover stopped without saying why:"; echo "$OUT" | tail -3; exit 1; }

[ "$(public_ids)" = "$PUBLIC_IDS" ] || { echo "FAIL: the test run used up public index_files ids"; exit 1; }

echo "PASS"
