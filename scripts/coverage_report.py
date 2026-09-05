#!/usr/bin/env python3
"""
Aggregate the coverage_log table into a "what have we ingested so far" report.

Reads Postgres directly (psql via docker compose). Prints:
  - running totals: files parsed, rate rows, distinct billing codes, NPIs, TINs,
    distinct network_names / plan_states
  - a per-file contribution table (most recent first)
  - suspicious completions: `completed` files that parsed to zero rows, from a
    sub-10 KB payload, or whose (rate, provider) row counts are byte-identical to
    another distinct file's — the signature of a silently-partial extract (#52).

Exit code is 1 when the suspicious-completions section is non-empty (so CI / a
post-batch hook can gate on it), unless --no-fail is passed.

Usage: python3 scripts/coverage_report.py [--schema public] [--limit 40] [--no-fail]
"""
import argparse
import json
import subprocess
import sys


def psql_json(sql, schema):
    out = subprocess.run(
        ["docker", "compose", "exec", "-T",
         "-e", f"PGOPTIONS=-c search_path={schema}",
         "db", "psql", "-U", "postgres", "-d", "honest_healthcare", "-tA",
         "-c", f"SELECT coalesce(json_agg(t), '[]') FROM ({sql.rstrip().rstrip(';')}) t"],
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
    ap.add_argument("--no-fail", action="store_true",
                    help="exit 0 even when suspicious completions are found")
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

    # Suspicious completions (#52). coverage_log.file_id is UNIQUE (migration
    # 004), so DISTINCT ON is now belt-and-suspenders — it also collapses any
    # duplicate left by the pre-enforcement schema.
    flagged = psql_json("""
        WITH latest AS (
          SELECT DISTINCT ON (c.file_id)
                 c.file_id,
                 left(regexp_replace(c.location, '\\?.*', ''), 60) AS file,
                 c.compressed_bytes, c.n_rate_rows AS rr, c.n_provider_rows AS pr,
                 coalesce(c.notes, '') LIKE '%dropped%' AS had_drops
          FROM coverage_log c
          JOIN index_files i ON i.id = c.file_id AND i.status = 'completed'
          ORDER BY c.file_id, c.parsed_at DESC
        ),
        dup AS (
          SELECT rr, pr FROM latest
          WHERE rr > 0 OR pr > 0
          GROUP BY rr, pr HAVING count(*) > 1
        )
        SELECT l.file_id, l.file, l.compressed_bytes, l.rr, l.pr,
               (l.rr = 0 AND l.pr = 0 AND NOT l.had_drops)              AS zero_rows,
               (l.compressed_bytes IS NOT NULL AND l.compressed_bytes < 10240) AS tiny,
               EXISTS (SELECT 1 FROM dup d WHERE d.rr = l.rr AND d.pr = l.pr) AS dup_stats
        FROM latest l
        WHERE (l.rr = 0 AND l.pr = 0 AND NOT l.had_drops)
           OR (l.compressed_bytes IS NOT NULL AND l.compressed_bytes < 10240)
           OR EXISTS (SELECT 1 FROM dup d WHERE d.rr = l.rr AND d.pr = l.pr)
        ORDER BY l.rr, l.pr, l.file_id
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
            print(f"| {r['file_id']} | {r['file']} | {mb:.1f} | {(r['n_rate_rows'] or 0):,} | "
                  f"{(r['n_provider_rows'] or 0):,} | {r['n_new_billing_codes'] or 0} | {(r['n_new_npis'] or 0):,} | "
                  f"{(r['networks'] or '')[:50]} | {'y' if r['parquet_retained'] else 'n'} |")
    else:
        print("\n(no coverage_log rows yet — run a parse batch)")

    if flagged:
        print(f"\n## ⚠️  Suspicious completions — {len(flagged)} file(s)\n")
        print("Files marked `completed` that may hold partial data (issue #52). "
              "Investigate, then `make db-reset WHAT=failed` or re-parse by id.\n")
        print("| file_id | file | KB | rate rows | prov rows | why |")
        print("|---|---|---|---|---|---|")
        for r in flagged:
            why = []
            if r["zero_rows"]:
                why.append("zero rows")
            if r["tiny"]:
                why.append("payload < 10 KB")
            if r["dup_stats"]:
                why.append("row counts shared with another file")
            kb = (r["compressed_bytes"] or 0) / 1024
            print(f"| {r['file_id']} | {r['file']} | {kb:.1f} | {(r['rr'] or 0):,} | {(r['pr'] or 0):,} | {'; '.join(why)} |")
        if not args.no_fail:
            sys.exit(1)
    else:
        print("\n✓ no suspicious completions")


if __name__ == "__main__":
    main()
