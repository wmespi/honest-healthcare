#!/usr/bin/env bash
# Hermetic ETL end-to-end test with full teardown.
#
# Runs the real Phase-2 parse in test mode, writing Parquet under data-test/.
# It parses a committed *.json.gz fixture and verifies the price / group_set
# rows, the network partitioning and the parse record. Then it discovers a
# served index and checks that parse downloads only a file a targets file
# names, downloads it again only once its Last-Modified changes, leaves a
# failed file alone until it's named, keeps a failed file's network out of the
# rates it publishes while every other network publishes, and stops on a parse
# that stopped part way. Cleans up everything on exit, so nothing accumulates
# on disk.
set -euo pipefail

cd "$(dirname "$0")/../.."
source tests/e2e/lib.sh

FIXTURE="extraction/testdata/fixtures/synthetic.json.gz"
PORT=8765
HOST="http://127.0.0.1:$PORT"

cleanup() {
  echo "→ teardown"
  serve_stop "$PORT"
  # Delete through the ingestion container - it wrote these files as root, so a host-side
  # rm fails under a bind mount on Linux (CI). Falls back to a host rm if needed.
  docker compose exec -T ingestion rm -rf /app/data-test/raw/anthem /app/data-test/ingestion/reference/exchange_plans_ga.parquet \
    /app/data-test/ingestion/reference/exchange_networks_ga.parquet /app/data-test/ingestion/payer_rates \
    /app/data-test/raw/e2e_targets.yaml /app/data-test/raw/e2e_targets_both.yaml /app/data-test/raw/e2e_http 2>/dev/null \
    || rm -rf data-test/raw/anthem data-test/raw/e2e_targets.yaml data-test/raw/e2e_targets_both.yaml data-test/raw/e2e_http
}
trap cleanup EXIT

echo "→ unit tests"
docker compose exec -T ingestion go test ./...

cleanup  # start from a clean slate
store_mark

echo "→ parse fixture (test isolation)"
docker compose exec -T ingestion go run . parse -test -fixture "$FIXTURE"

echo "→ verify parquet output (via DuckDB)"
RATE_ROWS=$(docker compose exec -T ingestion python3 -c "
import duckdb, glob
prices = glob.glob('/app/data-test/raw/anthem/prices/**/*.parquet', recursive=True)
gsets  = glob.glob('/app/data-test/raw/anthem/group_sets/*.parquet')
assert prices, 'no prices parquet written'
assert gsets, 'no group_sets parquet written'
assert any('/net=' in f for f in prices), f'prices not partitioned by net: {prices}'
con = duckdb.connect()
P = \"read_parquet('/app/data-test/raw/anthem/prices/**/*.parquet', hive_partitioning=1)\"
G = \"read_parquet('/app/data-test/raw/anthem/group_sets/*.parquet')\"
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

record() {  # record <file name> <column>: that column of the file's parse record, or ""
  docker compose exec -T ingestion python3 -c "
import duckdb
r = duckdb.connect().execute(\"SELECT $2::VARCHAR FROM read_parquet('/app/data-test/raw/anthem/parses/*.parquet') WHERE name = '$1'\").fetchall()
print(r[0][0] if r and r[0][0] is not None else '')"
}

echo "→ verify the parse record, saved under the file's name"
docker compose exec -T ingestion test -f /app/data-test/raw/anthem/parses/synthetic.parquet \
  || { echo "FAIL: the fixture's record isn't parses/synthetic.parquet"; exit 1; }
docker compose exec -T ingestion test -f /app/data-test/raw/anthem/codes/synthetic.parquet \
  || { echo "FAIL: the fixture's codes aren't codes/synthetic.parquet"; exit 1; }
[ "$(record synthetic.json.gz status)" = "parsed" ] || { echo "FAIL: the fixture's record says '$(record synthetic.json.gz status)', want parsed"; exit 1; }
[ "$(record synthetic.json.gz price_rows)" = "$N" ] || { echo "FAIL: the record counts $(record synthetic.json.gz price_rows) price rows, the Parquet $N"; exit 1; }

echo "→ parse downloads only the files a targets file names"
# The index links both files to a Blue Value plan, the way it links Blue Value
# to out-of-state shards. Only the named one is downloaded, from a file server
# whose Last-Modified stands in for Anthem's; the other is never touched. It
# also links Pathway's plan to its own file, which a later step names.
SHARD_NAME="2026-10_301_71B0_in-network-rates_01_of_02.json.gz"
mkdir -p data-test/raw/e2e_http  # host-made, so the host can copy into it under a Linux bind mount
docker compose exec -T ingestion python3 - "$HOST" "$SHARD_NAME" <<'EOF'
import gzip, json, os, sys, duckdb
host, shard = sys.argv[1], sys.argv[2]
ref = '/app/data-test/ingestion/reference'
os.makedirs(ref, exist_ok=True)
con = duckdb.connect()
con.execute(f"""COPY (FROM (VALUES ('45334GA0020008', '45334-GAN001'), ('49046GA0700053', '49046-GAN008')) t(plan_id, network_id))
                TO '{ref}/exchange_plans_ga.parquet' (FORMAT parquet)""")
con.execute(f"""COPY (FROM (VALUES ('45334-GAN001', '45334', 'Blue Value'), ('49046-GAN008', '49046', 'Pathway')) t(network_id, issuer_id, name))
                TO '{ref}/exchange_networks_ga.parquet' (FORMAT parquet)""")
os.makedirs('/app/data-test/raw/e2e_http', exist_ok=True)
def plan(plan_id): return {"plan_name": "P", "plan_id_type": "HIOS", "plan_id": plan_id, "plan_market_type": "individual"}
files = [{"description": "test", "location": f"{host}/GA_NAMED.json.gz"},
         {"description": "test", "location": f"{host}/{shard}?Expires=1"}]
pathway = [{"description": "test", "location": f"{host}/GA_PATHWAY.json.gz"}]
with gzip.open('/app/data-test/raw/e2e_http/index.json.gz', 'wt') as f:
    json.dump({"reporting_structure": [{"reporting_plans": [plan("45334GA0020008")], "in_network_files": files},
                                       {"reporting_plans": [plan("49046GA0700053")], "in_network_files": pathway}]}, f)
EOF
cp "ingestion/etl/$FIXTURE" data-test/raw/e2e_http/GA_NAMED.json.gz
touch -t 202610170400 data-test/raw/e2e_http/GA_NAMED.json.gz
serve_start e2e_http "$PORT"
docker compose exec -T ingestion go run . discover -test -index-url "$HOST/index.json.gz"
printf 'targets:\n  - name: Blue Value\n    exchange_networks: ["45334-GAN001"]\n    files: ["GA_NAMED.json.gz"]\n' > data-test/raw/e2e_targets.yaml
parse_named() { docker compose exec -T ingestion go run . parse -test -targets data-test/raw/e2e_targets.yaml; }

parse_named
echo "   named file: $(record GA_NAMED.json.gz status)  unnamed file: '$(record "$SHARD_NAME" status)'"
[ "$(record GA_NAMED.json.gz status)" = "parsed" ] || { echo "FAIL: the named file wasn't parsed"; exit 1; }
[ -z "$(record "$SHARD_NAME" status)" ] || { echo "FAIL: parse touched a file the targets don't name"; exit 1; }
READ=$(record GA_NAMED.json.gz anthem_updated_at)
DONE=$(record GA_NAMED.json.gz finished_at)
[ -n "$READ" ] || { echo "FAIL: parse didn't record which update of the file it read"; exit 1; }

echo "→ a parsed file isn't downloaded again until it's updated"
parse_named
[ "$(record GA_NAMED.json.gz finished_at)" = "$DONE" ] || { echo "FAIL: parse downloaded an unchanged file again"; exit 1; }
touch -t 202610310400 data-test/raw/e2e_http/GA_NAMED.json.gz
parse_named
echo "   read the update from $READ, then $(record GA_NAMED.json.gz anthem_updated_at)"
[ "$(record GA_NAMED.json.gz finished_at)" != "$DONE" ] || { echo "FAIL: parse didn't download the updated file"; exit 1; }
[ "$(record GA_NAMED.json.gz anthem_updated_at)" != "$READ" ] || { echo "FAIL: parse didn't record the update it read"; exit 1; }
[ -z "$(record "$SHARD_NAME" status)" ] || { echo "FAIL: parse touched a file the targets don't name"; exit 1; }

echo "→ a failed file waits until someone names it"
# Half the file fails the completeness check.
head -c $(( $(wc -c < "ingestion/etl/$FIXTURE") / 2 )) "ingestion/etl/$FIXTURE" > data-test/raw/e2e_http/GA_NAMED.json.gz
touch -t 202611010400 data-test/raw/e2e_http/GA_NAMED.json.gz
parse_named
[ "$(record GA_NAMED.json.gz status)" = "failed" ] || { echo "FAIL: a truncated download wasn't recorded as failed"; exit 1; }
FAILED=$(record GA_NAMED.json.gz finished_at)
cp "ingestion/etl/$FIXTURE" data-test/raw/e2e_http/GA_NAMED.json.gz
touch -t 202611020400 data-test/raw/e2e_http/GA_NAMED.json.gz
parse_named
[ "$(record GA_NAMED.json.gz finished_at)" = "$FAILED" ] || { echo "FAIL: parse retried a failed file nobody named"; exit 1; }
docker compose exec -T ingestion go run . parse -test -files GA_NAMED.json.gz
[ "$(record GA_NAMED.json.gz status)" = "parsed" ] || { echo "FAIL: naming the failed file didn't parse it"; exit 1; }

echo "→ a failed file stales only its own network"
# Pathway's file is the fixture with Blue Value's label renamed to Pathway's,
# cut in half for its first parse. Parse still exits 0 and names the file and
# its network, and the publish step prices Blue Value and leaves Pathway out.
gzip -dc "ingestion/etl/$FIXTURE" | sed 's/GA Blue Value HIX Individual Network/Pathway PCP Copay Choice/g' \
  | gzip > data-test/raw/e2e_http/pathway.json.gz
cut_pathway() {  # cut_pathway <touch time>: serve half of Pathway's file, updated then
  head -c $(( $(wc -c < data-test/raw/e2e_http/pathway.json.gz) / 2 )) data-test/raw/e2e_http/pathway.json.gz \
    > data-test/raw/e2e_http/GA_PATHWAY.json.gz
  touch -t "$1" data-test/raw/e2e_http/GA_PATHWAY.json.gz
}
cut_pathway 202611010400
BOTH=data-test/raw/e2e_targets_both.yaml
printf '%s\n' 'targets:' \
  '  - name: Blue Value' '    exchange_networks: ["45334-GAN001"]' '    files: ["GA_NAMED.json.gz"]' \
  '    network_patterns: ["GA Blue Value HIX*"]' \
  '  - name: Pathway' '    exchange_networks: ["49046-GAN008"]' '    files: ["GA_PATHWAY.json.gz"]' \
  '    network_patterns: ["Pathway PCP Copay Choice*"]' > "$BOTH"
parse_both() { docker compose exec -T ingestion go run . parse -test -limit 10 -targets "$BOTH" 2>&1; }
publish_both() { docker compose exec -T -w /app ingestion python3 -m ingestion.anthem_rates --test --targets "$BOTH" 2>&1; }
networks() {  # each published network as "<id> <status> <file_updated_at date>", one per line
  docker compose exec -T ingestion python3 -c "
import duckdb
con = duckdb.connect(); con.execute(\"SET TimeZone = 'UTC'\")
for r in con.execute(\"SELECT network_id, status, coalesce(strftime(file_updated_at, '%Y-%m-%d'), '-') FROM '/app/data-test/ingestion/payer_rates/anthem/networks.parquet' ORDER BY ALL\").fetchall():
    print(*r)"
}
OUT=$(parse_both) || { echo "FAIL: a failed target file stopped parse:"; echo "$OUT" | tail -5; exit 1; }
echo "$OUT" | grep -q "║ GA_PATHWAY.json.gz (49046-GAN008) failed on" \
  || { echo "FAIL: parse's summary doesn't name the failed file and its network:"; echo "$OUT" | tail -12; exit 1; }
OUT=$(publish_both) || { echo "FAIL: one network's failed file stopped the publish step:"; echo "$OUT" | tail -5; exit 1; }
echo "$OUT" | grep -q "49046-GAN008 has no rates, so nothing is published for it: the latest parse of GA_PATHWAY.json.gz" \
  || { echo "FAIL: the publish step didn't say why Pathway has no rates:"; echo "$OUT"; exit 1; }
[ "$(networks)" = "$(printf '45334-GAN001 current 2026-11-02\n49046-GAN008 missing -')" ] \
  || { echo "FAIL: networks.parquet says"; networks; exit 1; }

echo "→ a failed newer copy keeps its network's rates, marked stale"
cp data-test/raw/e2e_http/pathway.json.gz data-test/raw/e2e_http/GA_PATHWAY.json.gz
touch -t 202611020400 data-test/raw/e2e_http/GA_PATHWAY.json.gz
docker compose exec -T ingestion go run . parse -test -files GA_PATHWAY.json.gz >/dev/null 2>&1
cut_pathway 202611030400
parse_both >/dev/null || { echo "FAIL: a failed newer copy stopped parse"; exit 1; }
publish_both >/dev/null || { echo "FAIL: a failed newer copy stopped the publish step"; exit 1; }
[ "$(networks)" = "$(printf '45334-GAN001 current 2026-11-02\n49046-GAN008 stale 2026-11-02')" ] \
  || { echo "FAIL: networks.parquet says"; networks; exit 1; }
OUT=$(parse_both)
echo "$OUT" | grep -q "its rates on disk are the copy Anthem updated on 2026-11-02" \
  || { echo "FAIL: the next run doesn't say the waiting file's rates are from its earlier copy:"; echo "$OUT" | tail -8; exit 1; }

echo "→ a parse that stopped part way stops parse"
docker compose exec -T ingestion mkdir -p /app/data-test/raw/anthem/.inflight/GA_NAMED
if OUT=$(parse_named 2>&1); then
  echo "FAIL: parse ran over a file whose last parse stopped part way"; exit 1
fi
echo "$OUT" | grep -q "stopped part way" || { echo "FAIL: parse stopped without saying why:"; echo "$OUT" | tail -3; exit 1; }

store_untouched

echo "PASS"
