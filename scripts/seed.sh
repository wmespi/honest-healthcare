#!/usr/bin/env bash
# Seed data/ with the committed synthetic MRF so a fresh clone has a working API
# before any real ETL runs. Idempotent — a no-op once data/anthem/prices exists.
#
# This is sample data (fake NPIs, two networks, five codes). For real rates run
#   make discover && make parse
set -euo pipefail
cd "$(dirname "$0")/.."

FIXTURE="extraction/testdata/fixtures/synthetic.json.gz"

if docker compose exec -T serving python3 -c \
  "import glob,sys; sys.exit(0 if glob.glob('/app/data/anthem/prices/**/*.parquet', recursive=True) else 1)" 2>/dev/null; then
  echo "→ data/anthem/prices already populated — nothing to seed"
  echo "  (rm -rf data/anthem to re-seed, or run 'make parse' for real rates)"
  exit 0
fi

echo "→ seeding data/ from $FIXTURE"

# -all-npis because the synthetic NPIs are invented and no NPPES roster holds
# them. The parse probe still applies — the fixture passes it, because it carries
# the target plan's network label.
docker compose exec -T etl go run . parse -fixture "$FIXTURE" -all-npis

echo "→ done — GET http://localhost:8000/ now reports rows."
echo "  This is synthetic sample data. Real rates: make discover && make parse"
