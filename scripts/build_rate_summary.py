"""Precompute the browse-layer summary Parquet from the landed rate store.

The detail endpoints (`/rates/distribution?network=`, `/rates/providers?network=`)
stay on raw partition-pruned Parquet. The browse endpoints (`/networks`,
`/billing_codes`, `/procedure_categories`) aggregate across *all* networks —
`prices ⨝ group_sets` is ~1e9 rows at three files and OOM-kills the API
(issue #10). This builds two small tables they read instead:

  summary/rate_summary.parquet
    payer | network_name | net | billing_code_type | billing_code | setting | n_rates
      — one row per priced (network, code, setting); `/networks` sums n_rates.

  summary/code_rollup.parquet
    payer | billing_code_type | billing_code | n_provider_groups | n_rates
      — one row per priced code; n_provider_groups is SUM of the code's rosters'
        sizes (a ranking hint — over-counts a group in several of a code's
        rosters, same as the pre-summary VOL_CTE). Computed against a
        pre-aggregated per-roster size table so it stays bounded at 1e9+
        group_set edges; an exact distinct count is a later refinement.

Run after a parse batch:  make build-summary   (TEST=1 for data-test/)

Rebuilt whole each time (a few seconds now, ~minutes at 20 GB). Incremental
per-file partials are a follow-up.
"""
import argparse
import os
import time

import duckdb

PAYER = "anthem"  # single payer today; the column is here for multi-payer (#10)


def build(data_dir: str) -> None:
    anthem = f"{data_dir}/anthem"
    out = f"{anthem}/summary"
    os.makedirs(out, exist_ok=True)

    prices = f"read_parquet('{anthem}/prices/**/*.parquet', union_by_name=true, hive_partitioning=1)"
    gsets = f"read_parquet('{anthem}/group_sets/*.parquet', union_by_name=true)"

    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET memory_limit = '{os.getenv('DUCKDB_MEMORY_LIMIT', '5GB')}'")
    con.execute(f"SET temp_directory = '{os.getenv('DUCKDB_TMP', '/tmp/duckdb_spill')}'")
    con.execute(f"SET max_temp_directory_size = '{os.getenv('DUCKDB_TMP_MAX', '80GiB')}'")
    os.makedirs(os.getenv("DUCKDB_TMP", "/tmp/duckdb_spill"), exist_ok=True)

    t0 = time.time()

    # ── rate_summary — n_rates per (network, code, setting). prices alone, no fan-out.
    con.execute(f"""
        COPY (
            SELECT '{PAYER}' AS payer, network_name, net,
                   billing_code_type, billing_code, setting,
                   COUNT(*) AS n_rates
            FROM {prices}
            GROUP BY ALL
        ) TO '{out}/rate_summary.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)
    rs = con.execute(f"SELECT COUNT(*), SUM(n_rates) FROM read_parquet('{out}/rate_summary.parquet')").fetchone()
    print(f"  rate_summary  {rs[0]:>9,} rows  ({rs[1]:,} rate-rows)  {time.time()-t0:.1f}s")

    # ── code_rollup — provider-group volume per code (+ n_rates), a ranking hint.
    #
    # n_provider_groups is SUM of the code's rosters' sizes, computed against a
    # pre-aggregated per-roster size table (~1e5 rows) — NOT a join to the raw
    # group_sets edges. Joining code_sets (~1e6) to group_sets (>1e9 at scale)
    # to get an exact/HLL distinct count spills tens of GB and OOMs; the roster
    # pre-agg keeps this bounded. It over-counts a provider group that sits in
    # several of a code's rosters (same as the pre-summary VOL_CTE) — fine for
    # ordering /billing_codes and /procedure_categories.
    t1 = time.time()
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
    print(f"  code_rollup   {cr:>9,} rows  {time.time()-t1:.1f}s")
    print(f"  total {time.time()-t0:.1f}s → {out}/")


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
