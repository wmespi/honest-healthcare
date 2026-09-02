"""Precompute the browse-layer summary Parquet from the landed rate store.

The rule: **only a single-code drill-down** (`/rates/*` with a `billing_code`)
queries `prices` live — then it prunes to a handful of rows and the
`⨝ group_sets` fanout is cheap. **Everything else — the network overview,
`/networks`, `/billing_codes`, `/procedure_categories` — reads a precomputed
table here**, never `prices` (645M+ rows) or `prices ⨝ group_sets` (1e9+ edges),
which OOM-kill the API (issue #10).

  summary/rate_hist.parquet
    payer | net | network_name | billing_code_type | billing_code | setting
      | scope | bucket | n
    — a pre-bucketed rate histogram ($25 buckets to $5000, then one overflow
      bucket). The one heavy scan of `prices`, all-scalar (COUNT). The network
      overview's bars ARE this table; the serving layer derives p10/median/p90
      from its CDF at read time (a code has ~20-200 buckets — trivial). Exact
      per-group percentiles at build time OOM: millions of groups × any
      non-scalar accumulator (t-digest included).
      `scope` = 'outpatient_prof' for outpatient professional fee-for-service
      dollar rates (what the consumer views compare — see serving/data_sources
      .outpatient_scope), 'other' for everything else. The no-code network
      overview filters to 'outpatient_prof'; rate_summary / code_rollup roll up
      across both (they answer "what exists", not "what does it cost").

  summary/rate_summary.parquet
    payer | network_name | net | billing_code_type | billing_code | setting
      | n_rates | min_rate | max_rate | avg_rate
    — one row per priced (network, code, setting), rolled up from rate_hist.
      min/max/avg are bucket-approximate (± $25). `/networks` sums n_rates.
      NOTE: no all-settings ('*') rollup row — every consumer that sums n_rates
      would double-count it. Roll settings up at read time (a code has a
      handful).

  summary/code_rollup.parquet
    payer | billing_code_type | billing_code | n_provider_groups | n_rates
      — one row per priced code; n_provider_groups is SUM of the code's rosters'
        sizes (a ranking hint — over-counts a group in several of a code's
        rosters, same as the pre-summary VOL_CTE). Computed against a
        pre-aggregated per-roster size table so it stays bounded at 1e9+
        group_set edges; an exact distinct count is a later refinement (#48).

Run after a parse batch:  make build-summary   (TEST=1 for data-test/)

Rebuilt whole each time (~3 min at 645M price rows). Incremental per-file
partials are a follow-up.
"""
import argparse
import os
import shutil
import time

import duckdb

PAYER = "anthem"      # single payer today; the column is here for multi-payer (#10)
HIST_WIDTH = 25       # $ bucket width
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

    # ── rate_hist — the one heavy scan. Pre-bucketed, all-scalar COUNT.
    con.execute(f"""
        COPY (
            SELECT '{PAYER}' AS payer, net, network_name,
                   billing_code_type, billing_code, setting,
                   CASE WHEN billing_class = 'professional'
                             AND setting IN ('outpatient', 'both')
                             AND negotiation_arrangement = 'ffs'
                             AND negotiated_type IN ('fee schedule', 'negotiated')
                        THEN 'outpatient_prof' ELSE 'other'
                   END AS scope,
                   CASE WHEN negotiated_rate >= {HIST_CAP} THEN {HIST_CAP}
                        WHEN negotiated_rate < 0 THEN 0
                        ELSE FLOOR(negotiated_rate / {HIST_WIDTH}) * {HIST_WIDTH}
                   END AS bucket,
                   COUNT(*)::BIGINT AS n
            FROM {prices}
            GROUP BY net, network_name, billing_code_type, billing_code, setting, scope, bucket
        ) TO '{out}/rate_hist.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)
    h = con.execute(f"SELECT COUNT(*), SUM(n) FROM {hist}").fetchone()
    print(f"  rate_hist      {h[0]:>10,} rows  ({h[1]:,} rate-rows)  {time.time()-t0:.1f}s")

    # ── rate_summary — scalar rollup of rate_hist (one row per net/code/setting).
    t1 = time.time()
    con.execute(f"""
        COPY (
            SELECT payer, network_name, net, billing_code_type, billing_code, setting,
                   SUM(n)                                     AS n_rates,
                   MIN(bucket)                                AS min_rate,
                   MAX(bucket) + {HIST_WIDTH}                 AS max_rate,
                   ROUND(SUM(bucket * n)::DOUBLE / SUM(n), 2) AS avg_rate
            FROM {hist}
            GROUP BY ALL
        ) TO '{out}/rate_summary.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)
    rs = con.execute(f"SELECT COUNT(*), SUM(n_rates) FROM read_parquet('{out}/rate_summary.parquet')").fetchone()
    print(f"  rate_summary   {rs[0]:>10,} rows  ({rs[1]:,} rate-rows)  {time.time()-t1:.1f}s")

    # ── code_rollup — provider-group volume per code (+ n_rates), a ranking hint.
    #
    # n_provider_groups is SUM of the code's rosters' sizes, computed against a
    # pre-aggregated per-roster size table (~1e5 rows) — NOT a join to the raw
    # group_sets edges. Joining code_sets (~1e6) to group_sets (>1e9 at scale)
    # for an exact/HLL distinct count spills tens of GB and OOMs; the roster
    # pre-agg keeps it bounded. Over-counts a group in several of a code's
    # rosters (same as the pre-summary VOL_CTE) — fine for ordering. Exact
    # distinct count: #48.
    t2 = time.time()
    con.execute(f"""
        COPY (
            WITH roster AS (
                SELECT file_id, group_set_id, COUNT(*)::BIGINT AS n_groups
                FROM {gsets} GROUP BY 1, 2
            ),
            code_sets AS (
                SELECT DISTINCT billing_code_type, billing_code, file_id, group_set_id
                FROM {prices}
            ),
            groups AS (
                SELECT cs.billing_code_type, cs.billing_code,
                       SUM(roster.n_groups) AS n_provider_groups
                FROM code_sets cs
                JOIN roster USING (file_id, group_set_id)
                GROUP BY 1, 2
            ),
            rates AS (
                SELECT billing_code_type, billing_code, CAST(SUM(n_rates) AS BIGINT) AS n_rates
                FROM read_parquet('{out}/rate_summary.parquet')
                GROUP BY 1, 2
            )
            SELECT '{PAYER}' AS payer, r.billing_code_type, r.billing_code,
                   CAST(COALESCE(g.n_provider_groups, 0) AS BIGINT) AS n_provider_groups,
                   r.n_rates
            FROM rates r
            LEFT JOIN groups g USING (billing_code_type, billing_code)
        ) TO '{out}/code_rollup.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)
    cr = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out}/code_rollup.parquet')").fetchone()[0]
    print(f"  code_rollup    {cr:>10,} rows  {time.time()-t2:.1f}s")
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
