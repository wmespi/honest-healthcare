"""Precompute the browse-layer summary Parquet from the landed rate store.

The rule: **only a single-code drill-down** (`/rates/*` with a `billing_code`)
queries `prices` live — then it prunes to a handful of rows and the
`⨝ group_sets` fanout is cheap. **Everything else — the network overview,
`/networks`, `/billing_codes`, `/procedure_categories`, `/rates/by_network` —
reads a precomputed table here**, never `prices` (645M+ rows) or
`prices ⨝ group_sets` (1e9+ edges), which OOM-kill the API (issue #10).

  summary/rate_hist.parquet
    payer | net | network_name | billing_code_type | billing_code | setting
      | bucket | n
    — a pre-bucketed rate histogram ($25 buckets to $5000, then one overflow
      bucket). The one heavy scan of `prices`, all-scalar (COUNT). Everything
      below derives from it; the network overview's bars ARE this table; the
      serving layer computes p10/median/p90 from its CDF at read time (a code
      has ~20-200 buckets — trivial). Exact per-group percentiles at build time
      OOM: millions of groups × any non-scalar accumulator (t-digest included).

  summary/rate_summary.parquet
    payer | net | network_name | billing_code_type | billing_code | setting
      | n_rates | min_rate | max_rate | avg_rate
    — scalar rollup of rate_hist, + a `setting = '*'` row across all settings.
      min/max/avg are bucket-approximate (± $25). `/networks` sums n_rates.

  summary/code_rollup.parquet       payer | billing_code_type | billing_code
                                      | n_provider_groups | n_rates
  summary/net_code_rollup.parquet   payer | net | network_name
                                      | billing_code_type | billing_code | n_provider_groups
    — roster-size sums (a ranking hint — over-counts a group in several rosters,
      same as the pre-summary VOL_CTE), via a per-roster size table so they stay
      bounded at 1e9+ edges. `/billing_codes`, `/procedure_categories`,
      `/rates/by_network` n_groups.

Run after a parse batch:  make build-summary   (TEST=1 for data-test/)
Rebuilt whole each time. Incremental per-file partials are a follow-up.
"""
import argparse
import os
import shutil
import time

import duckdb

PAYER = "anthem"      # single payer today; the column is here for multi-payer (#10)
HIST_WIDTH = 25       # $ bucket
HIST_CAP = 5000       # rates >= this land in one overflow bucket


def build(data_dir: str) -> None:
    anthem = f"{data_dir}/anthem"
    out = f"{anthem}/summary"
    os.makedirs(out, exist_ok=True)

    prices = f"read_parquet('{anthem}/prices/**/*.parquet', union_by_name=true, hive_partitioning=1)"
    gsets = f"read_parquet('{anthem}/group_sets/*.parquet', union_by_name=true)"
    hist = f"read_parquet('{out}/rate_hist.parquet')"

    # Spill onto the data volume (host disk), not the container's small /tmp.
    spill = os.getenv("DUCKDB_TMP", f"{data_dir}/.duckdb_spill")
    os.makedirs(spill, exist_ok=True)

    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET memory_limit = '{os.getenv('DUCKDB_MEMORY_LIMIT', '4GB')}'")
    con.execute(f"SET threads = {os.getenv('DUCKDB_THREADS', '4')}")
    con.execute(f"SET temp_directory = '{spill}'")
    con.execute(f"SET max_temp_directory_size = '{os.getenv('DUCKDB_TMP_MAX', '120GiB')}'")

    t0 = time.time()

    # ── rate_hist — the one heavy scan. Pre-bucketed, all-scalar.
    con.execute(f"""
        COPY (
            SELECT '{PAYER}' AS payer, net, network_name,
                   billing_code_type, billing_code, setting,
                   CASE WHEN negotiated_rate >= {HIST_CAP} THEN {HIST_CAP}
                        WHEN negotiated_rate < 0 THEN 0
                        ELSE FLOOR(negotiated_rate / {HIST_WIDTH}) * {HIST_WIDTH}
                   END AS bucket,
                   COUNT(*)::BIGINT AS n
            FROM {prices}
            GROUP BY net, network_name, billing_code_type, billing_code, setting, bucket
        ) TO '{out}/rate_hist.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)
    h = con.execute(f"SELECT COUNT(*), SUM(n) FROM {hist}").fetchone()
    print(f"  rate_hist         {h[0]:>10,} rows  ({h[1]:,} rate-rows)  {time.time()-t0:.1f}s")

    # ── rate_summary — scalar rollup of rate_hist, + a setting='*' row.
    t1 = time.time()
    con.execute(f"""
        COPY (
            SELECT payer, net, network_name, billing_code_type, billing_code,
                   COALESCE(setting, '*') AS setting,
                   SUM(n)                                   AS n_rates,
                   MIN(bucket)                              AS min_rate,
                   MAX(bucket) + {HIST_WIDTH}               AS max_rate,
                   ROUND(SUM(bucket * n)::DOUBLE / SUM(n), 2) AS avg_rate
            FROM {hist}
            GROUP BY GROUPING SETS (
                (payer, net, network_name, billing_code_type, billing_code, setting),
                (payer, net, network_name, billing_code_type, billing_code)
            )
        ) TO '{out}/rate_summary.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)
    rs = con.execute(
        f"SELECT COUNT(*), SUM(n_rates) FILTER (WHERE setting = '*') "
        f"FROM read_parquet('{out}/rate_summary.parquet')").fetchone()
    print(f"  rate_summary      {rs[0]:>10,} rows  ({rs[1]:,} rate-rows)  {time.time()-t1:.1f}s")

    # ── roster sizes — per group_set, once. Both rollups sum these.
    t2 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE roster AS
        SELECT file_id, group_set_id, COUNT(*)::BIGINT AS n_groups
        FROM {gsets} GROUP BY 1, 2
    """)

    con.execute(f"""
        COPY (
            WITH code_sets AS (
                SELECT DISTINCT billing_code_type, billing_code, file_id, group_set_id FROM {prices}
            ),
            groups AS (
                SELECT cs.billing_code_type, cs.billing_code,
                       SUM(roster.n_groups) AS n_provider_groups
                FROM code_sets cs JOIN roster USING (file_id, group_set_id)
                GROUP BY 1, 2
            ),
            rates AS (
                SELECT billing_code_type, billing_code, CAST(SUM(n_rates) AS BIGINT) AS n_rates
                FROM read_parquet('{out}/rate_summary.parquet') WHERE setting = '*'
                GROUP BY 1, 2
            )
            SELECT '{PAYER}' AS payer, r.billing_code_type, r.billing_code,
                   CAST(COALESCE(g.n_provider_groups, 0) AS BIGINT) AS n_provider_groups,
                   r.n_rates
            FROM rates r LEFT JOIN groups g USING (billing_code_type, billing_code)
        ) TO '{out}/code_rollup.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)
    cr = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out}/code_rollup.parquet')").fetchone()[0]
    print(f"  code_rollup       {cr:>10,} rows  {time.time()-t2:.1f}s")

    t3 = time.time()
    con.execute(f"""
        COPY (
            WITH code_sets AS (
                SELECT DISTINCT net, network_name, billing_code_type, billing_code, file_id, group_set_id
                FROM {prices}
            )
            SELECT '{PAYER}' AS payer, cs.net, cs.network_name,
                   cs.billing_code_type, cs.billing_code,
                   CAST(SUM(roster.n_groups) AS BIGINT) AS n_provider_groups
            FROM code_sets cs JOIN roster USING (file_id, group_set_id)
            GROUP BY 2, 3, 4, 5
        ) TO '{out}/net_code_rollup.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)
    ncr = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out}/net_code_rollup.parquet')").fetchone()[0]
    print(f"  net_code_rollup   {ncr:>10,} rows  {time.time()-t3:.1f}s")
    print(f"  total {time.time()-t0:.1f}s → {out}/")

    con.close()
    shutil.rmtree(spill, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR", "/app/data"))
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()
    data_dir = "/app/data-test" if args.test else args.data_dir
    if not os.path.exists(f"{data_dir}/anthem/prices"):
        raise SystemExit(f"no rate store at {data_dir}/anthem/prices — parse first")
    build(data_dir)


if __name__ == "__main__":
    main()
