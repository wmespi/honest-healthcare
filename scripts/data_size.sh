#!/usr/bin/env bash
# Data-consumption scorecard: rows + bytes for every Parquet table, the Postgres
# queue tables, and a project-wide total. Read-only. `make data-size`
# (JSON=1 for machine output).
set -euo pipefail
cd "$(dirname "$0")/.."

json=""
[[ "${1:-}" == "--json" ]] && json=1

# ── Parquet (DuckDB, in the serving container) ────────────────────────────
pq_out="$(docker compose exec -T -w /app serving python3 scripts/data_size.py "$@")"

# ── Postgres — exact COUNT(*) (pg_stat estimates lag) + total relation size ─
pg_raw="$(docker compose exec -T db psql -U postgres -d honest_healthcare -qtA -F $'\t' -c "
  SELECT name,
         pg_size_pretty(pg_total_relation_size(('public.'||name)::regclass)),
         pg_total_relation_size(('public.'||name)::regclass) AS bytes,
         n
  FROM (
    SELECT 'index_files'  AS name, count(*) n FROM public.index_files
    UNION ALL SELECT 'index_file_plans', count(*) FROM public.index_file_plans
    UNION ALL SELECT 'billing_codes', count(*) FROM public.billing_codes
    UNION ALL SELECT 'coverage_log',  count(*) FROM public.coverage_log
  ) q
  ORDER BY bytes DESC;
")"
pg_bytes="$(awk -F'\t' '{s+=$3} END{print s+0}' <<<"$pg_raw")"
pg_rows="$(awk -F'\t' '{s+=$4} END{print s+0}' <<<"$pg_raw")"

if [[ -n "$json" ]]; then
  PG_RAW="$pg_raw" PG_BYTES="$pg_bytes" PG_ROWS="$pg_rows" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
d["postgres"] = [
    {"table": r.split("\t")[0], "size_pretty": r.split("\t")[1],
     "bytes": int(r.split("\t")[2]), "rows": int(r.split("\t")[3])}
    for r in os.environ["PG_RAW"].splitlines() if r.strip()
]
pqb, pqr = d["total"]["bytes"], d["total"]["rows"]
pgb, pgr = int(os.environ["PG_BYTES"]), int(os.environ["PG_ROWS"])
d["project_total"] = {"bytes": pqb + pgb, "rows": pqr + pgr}
print(json.dumps(d, indent=2))
' <<<"$pq_out"
  exit 0
fi

# ── human ────────────────────────────────────────────────────────────────
grep -v '^__TOTALS__' <<<"$pq_out"
pq_bytes="$(sed -n 's/.*parquet_bytes=\([0-9]*\).*/\1/p' <<<"$pq_out")"
pq_rows="$(sed -n 's/.*parquet_rows=\([0-9]*\).*/\1/p' <<<"$pq_out")"

awk -F'\t' '
  BEGIN { printf "\n  %-40s %15s %12s\n", "Postgres table", "rows", "size";
          printf "  %s %s %s\n", "----------------------------------------", "---------------", "------------" }
  function commas(n,  s,r) { s=sprintf("%d", n); r="";
    while (length(s)>3) { r="," substr(s,length(s)-2) r; s=substr(s,1,length(s)-3) }
    return s r }
  { printf "  %-40s %15s %12s\n", $1, commas($4), $2 }
  END { print "\n  (size is table + indexes + TOAST; parse status churn bloats index_files —\n   VACUUM (FULL) reclaims it.)" }
' <<<"$pg_raw"

awk -v pqb="$pq_bytes" -v pqr="$pq_rows" -v pgb="$pg_bytes" -v pgr="$pg_rows" '
  function human(x,  u,i) { split("B KB MB GB TB", u); i=1;
    while (x>=1024 && i<5) { x/=1024; i++ } return sprintf("%.1f %s", x, u[i]) }
  function commas(n,  s,r) { s=sprintf("%d", n); r="";
    while (length(s)>3) { r="," substr(s,length(s)-2) r; s=substr(s,1,length(s)-3) }
    return s r }
  BEGIN {
    bar="========================================================================"
    printf "\n  %s\n", bar
    printf "  %-38s %17s %12s\n", "PROJECT DATA TOTAL (Parquet + Postgres)", \
           commas(pqr+pgr) " rows", human(pqb+pgb)
    printf "  %s\n\n", bar
  }'
