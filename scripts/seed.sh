#!/usr/bin/env bash
# Seed data/ with the committed synthetic MRF so a fresh clone has a working API
# before any real ETL runs. Idempotent — a no-op once data/anthem/prices exists.
#
# This is sample data (fake NPIs, two networks, five codes). For real rates run
#   make discover && make parse
set -euo pipefail
cd "$(dirname "$0")/.."

FIXTURE="extraction/testdata/fixtures/synthetic.json.gz"
LOCATION="fixture://synthetic-seed"
PSQL=(docker compose exec -T db psql -U postgres -d honest_healthcare -qtA -v ON_ERROR_STOP=1)

if docker compose exec -T serving python3 -c \
  "import glob,sys; sys.exit(0 if glob.glob('/app/data/anthem/prices/**/*.parquet', recursive=True) else 1)" 2>/dev/null; then
  echo "→ data/anthem/prices already populated — nothing to seed"
  echo "  (rm -rf data/anthem to re-seed, or run 'make parse' for real rates)"
  exit 0
fi

echo "→ seeding data/ from $FIXTURE"
FILE_ID=$("${PSQL[@]}" -c "
  INSERT INTO index_files (location, market_types, hios_issuer_ids, plan_states, status)
  VALUES ('$LOCATION', ARRAY['individual','group'], ARRAY['45334'], ARRAY['GA'], 'pending')
  ON CONFLICT (location) DO UPDATE SET status = 'pending'
  RETURNING id;")

docker compose exec -T etl go run . parse -file-ids "$FILE_ID" -fixture "$FIXTURE" -all-npis -all-networks

echo "→ done — GET http://localhost:8000/ now reports rows."
echo "  This is synthetic sample data. Real rates: make discover && make parse"
