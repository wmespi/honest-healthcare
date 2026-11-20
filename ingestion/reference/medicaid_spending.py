#!/usr/bin/env python3
"""
Build data/ingestion/reference/medicaid_ga.parquet - one row per Georgia NPI and
HCPCS code Medicaid shows billed in the latest 12 months of HHS's release:

  "Medicaid Provider Spending by HCPCS"
  https://opendata.hhs.gov/datasets/medicaid-provider-spending/

The find-care build reads it beside Medicare's utilization as evidence that a
doctor does a service (integration/inputs/biller_rates.py). Medicare can't see
children's or pregnancy care; Medicaid can.

Output columns:
  npi          the servicing NPI, else the billing NPI when servicing is blank
  hcpcs_cd     the code
  claim_lines  claim lines summed over the window
  through      the window's last month, YYYY-MM

Caveats (ingestion/reference/medicaid.md): rows under 12 claims are suppressed by
HHS, there is no state or place of service, and the data ends where the release does.

Usage:
  python3 -m ingestion.reference.medicaid_spending [--url URL | --file PATH]
                                                   [--data-dir data] [--test]
"""
import argparse
import os

import duckdb

from ._common import store_dir, write_parquet_atomic

URL = ("https://stopendataprod.blob.core.windows.net/datasets/medicaid-provider-spending/"
       "2026-02-09/dataset/medicaid-provider-spending.parquet")
WINDOW_MONTHS = 12


def build(con, src: str, nppes_paths: list, out_path: str) -> str:
    """Write the output from the Parquet at `src`, scoped to the NPIs in
    `nppes_paths` (NPPES's Georgia providers first) that aren't hospitals; return
    the window's last month."""
    source = f"read_parquet('{src}')"
    through = con.execute(f"SELECT max(CLAIM_FROM_MONTH) FROM {source}").fetchone()[0]
    if not through:
        raise SystemExit(f"{src} has no CLAIM_FROM_MONTH")
    npis = " UNION ".join(f"SELECT npi FROM read_parquet('{p}')" for p in nppes_paths)
    # A hospital's rows are its own facility claims, which Medicare's utilization,
    # the other evidence, doesn't carry either: they don't show who does a service.
    hospitals = f"SELECT npi FROM read_parquet('{nppes_paths[0]}') WHERE is_hospital"
    select_sql = f"""
        WITH ga AS ({npis} EXCEPT {hospitals}),
        window_start AS (
            SELECT strftime(CAST('{through}-01' AS DATE) - INTERVAL {WINDOW_MONTHS - 1} MONTH, '%Y-%m') AS month
        ),
        src AS (
            SELECT CASE WHEN NULLIF(TRIM(SERVICING_PROVIDER_NPI_NUM), '') IS NULL
                        THEN TRY_CAST(TRIM(BILLING_PROVIDER_NPI_NUM) AS BIGINT)
                        ELSE TRY_CAST(TRIM(SERVICING_PROVIDER_NPI_NUM) AS BIGINT) END AS npi,
                   TRIM(HCPCS_CODE) AS hcpcs_cd, TOTAL_CLAIM_LINES AS lines
            FROM {source}, window_start
            WHERE CLAIM_FROM_MONTH >= window_start.month AND CLAIM_FROM_MONTH <= '{through}'
        )
        SELECT npi, hcpcs_cd, sum(lines)::BIGINT AS claim_lines, '{through}' AS through
        FROM src
        WHERE npi IN (SELECT npi FROM ga) AND hcpcs_cd <> ''
        GROUP BY npi, hcpcs_cd
    """
    write_parquet_atomic(con, select_sql, out_path)
    return through


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=URL, help="override the source Parquet URL")
    ap.add_argument("--file", default=None, help="read a local Parquet instead of the URL")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    nppes = store_dir(args.data_dir, args.test, "nppes")
    providers = f"{nppes}/ga_providers.parquet"
    if not os.path.exists(providers):
        raise SystemExit(f"no {providers}: run `make reference STEP=nppes`")
    paths = [providers] + [p for p in [f"{nppes}/ga_locations.parquet"] if os.path.exists(p)]

    out = f"{store_dir(args.data_dir, args.test, 'reference')}/medicaid_ga.parquet"
    con = duckdb.connect()
    os.makedirs("/tmp/medicaid", exist_ok=True)
    con.execute("SET temp_directory='/tmp/medicaid'")
    con.execute("SET preserve_insertion_order = false")
    if args.file:
        src = args.file
    else:
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        src = args.url
    print(f"→ HHS Medicaid Provider Spending by HCPCS ({src})")
    through = build(con, src, paths, out)
    rows, npis, codes = con.execute(
        f"SELECT count(*), count(DISTINCT npi), count(DISTINCT hcpcs_cd) FROM read_parquet('{out}')").fetchone()
    print(f"→ wrote {out}\n  {rows:,} rows · {npis:,} NPIs · {codes:,} codes · "
          f"{WINDOW_MONTHS} months through {through}")


if __name__ == "__main__":
    main()
