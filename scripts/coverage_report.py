#!/usr/bin/env python3
"""
Aggregate the coverage_log table into a "what have we ingested so far" report.

Reads Postgres directly (psql via docker compose). Prints:
  - running totals: files parsed, rate rows, distinct billing codes, NPIs, TINs,
    distinct network_names / plan_states
  - a per-file contribution table (most recent first)

Usage: python3 scripts/coverage_report.py [--schema public] [--limit 40]
"""
import argparse
import json
import subprocess
import sys


def psql_json(sql, schema):
    full = f"SET search_path TO {schema}; " + sql
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres",
         "-d", "honest_healthcare", "-tAc",
         f"SELECT coalesce(json_agg(t), '[]') FROM ({full.rstrip(';')}) t"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        sys.exit(1)
    return json.loads(out.stdout.strip() or "[]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default="public")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    totals = psql_json("""
        SELECT
          count(*)                                        AS files_parsed,
          coalesce(sum(n_rate_rows), 0)                   AS rate_rows,
          coalesce(sum(n_provider_rows), 0)               AS provider_rows,
          coalesce(sum(n_new_billing_codes), 0)           AS distinct_billing_codes,
          coalesce(sum(n_new_npis), 0)                    AS distinct_npis,
          coalesce(sum(n_new_tins), 0)                    AS distinct_tins,
          coalesce(sum(n_ga_hospital_npis), 0)            AS ga_hospital_npis
        FROM coverage_log
    """, args.schema)
    agg = psql_json("""
        SELECT
          array_length(array_agg(DISTINCT nn), 1)  AS distinct_networks,
          array_length(array_agg(DISTINCT ps), 1)  AS distinct_plan_states
        FROM coverage_log,
             LATERAL unnest(coalesce(network_names, '{}')) AS nn,
             LATERAL unnest(coalesce(plan_states,  '{}')) AS ps
    """, args.schema)
    per_file = psql_json(f"""
        SELECT file_id, left(regexp_replace(location, '\\?.*', ''), 60) AS file,
               compressed_bytes, n_rate_rows, n_provider_rows,
               n_new_billing_codes, n_new_npis,
               array_to_string(network_names, ', ') AS networks,
               parquet_retained, parsed_at
        FROM coverage_log
        ORDER BY parsed_at DESC
        LIMIT {args.limit}
    """, args.schema)

    t = totals[0] if totals else {}
    a = agg[0] if agg else {}
    print(f"\n## Coverage so far — schema `{args.schema}`\n")
    print(f"- files parsed:        {t.get('files_parsed', 0)}")
    print(f"- rate rows:           {int(t.get('rate_rows', 0)):,}")
    print(f"- provider rows:       {int(t.get('provider_rows', 0)):,}")
    print(f"- distinct codes:      {t.get('distinct_billing_codes', 0)}")
    print(f"- distinct NPIs:       {int(t.get('distinct_npis', 0)):,}")
    print(f"- distinct TINs:       {int(t.get('distinct_tins', 0)):,}")
    print(f"- distinct networks:   {a.get('distinct_networks') or 0}")
    print(f"- distinct plan states:{a.get('distinct_plan_states') or 0}")
    print(f"- GA hospital NPIs:    {t.get('ga_hospital_npis', 0)}")

    if per_file:
        print("\n| file_id | file | MB | rate rows | prov rows | new codes | new NPIs | networks | kept |")
        print("|---|---|---|---|---|---|---|---|---|")
        for r in per_file:
            mb = (r["compressed_bytes"] or 0) / 1e6
            print(f"| {r['file_id']} | {r['file']} | {mb:.1f} | {r['n_rate_rows']:,} | "
                  f"{r['n_provider_rows']:,} | {r['n_new_billing_codes']} | {r['n_new_npis']:,} | "
                  f"{(r['networks'] or '')[:50]} | {'y' if r['parquet_retained'] else 'n'} |")
    else:
        print("\n(no coverage_log rows yet — run a parse batch)")


if __name__ == "__main__":
    main()
