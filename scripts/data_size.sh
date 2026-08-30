#!/usr/bin/env bash
# Data-consumption scorecard: rows + bytes for every Parquet table, plus the
# Postgres queue tables. Read-only. `make data-size`.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose exec -T -w /app serving python3 scripts/data_size.py "$@"

# Postgres side — row counts + total (table + indexes + TOAST) relation size.
# Exact COUNT(*) (pg_stat estimates lag) + total (table + indexes + TOAST) size.
docker compose exec -T db psql -U postgres -d honest_healthcare -qtA -F $'\t' -c "
  SELECT name,
         to_char(n, 'FM999,999,999,999'),
         pg_size_pretty(pg_total_relation_size(('public.'||name)::regclass))
  FROM (
    SELECT 'index_files'  AS name, count(*) n FROM public.index_files
    UNION ALL SELECT 'billing_codes', count(*) FROM public.billing_codes
    UNION ALL SELECT 'coverage_log',  count(*) FROM public.coverage_log
  ) q
  ORDER BY pg_total_relation_size(('public.'||name)::regclass) DESC;
" | awk -F'\t' '
  BEGIN { printf "\n  %-40s %15s %12s\n", "Postgres table", "rows", "size";
          printf "  %s %s %s\n", "----------------------------------------", "---------------", "------------" }
  { printf "  %-40s %15s %12s\n", $1, $2, $3 }
  END { print "\n  (size is table + indexes + TOAST; parse status churn bloats index_files —\n   VACUUM (FULL) reclaims it.)\n" }
'
