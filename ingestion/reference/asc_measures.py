#!/usr/bin/env python3
"""
Build the ambulatory surgery center (ASC) quality measures from CMS's
"Ambulatory Surgical Center Quality Measures - Facility" (4jcv-atw7), one row per
Georgia ASC and measure, and the national values from the "- National" file (wue8-3vwe):

  data/ingestion/reference/asc_measures_ga.parquet        npi | ccn | measure | score | compared | sample | period
  data/ingestion/reference/asc_measures_national.parquet  measure | national | median | period

Measures kept: ASC-9, ASC-11 (a rate, no comparison label), ASC-12, ASC-17, ASC-18 (risk-standardized
rate, CMS's comparison label, case count) and ASC-19 (a ratio, same). `score` is null where CMS
publishes "N/A"; a row with neither a score nor a label is not kept. `national` is the national
average for ASC-9 and ASC-11 (with the `median`) and the national rate or ratio for the others.
See ingestion/reference/asc-measures.md.

Usage: python3 -m ingestion.reference.asc_measures [--file FACILITY_CSV] [--national-file NATIONAL_CSV] [--data-dir data] [--test]
"""
import argparse

import duckdb

from ._common import store_dir, write_parquet_atomic
from ._pdc import fetch_dataset, require_columns

FACILITY, NATIONAL = "4jcv-atw7", "wue8-3vwe"

# measure -> (score column, comparison column, case-count column) in the facility file
RATES = {"ASC-9": "ASC-9 Rate*", "ASC-11": "ASC-11 Rate*"}
RISK_ADJUSTED = {"ASC-12": "RSHV Rate", "ASC-17": "RSHV Rate", "ASC-18": "RSHV Rate", "ASC-19": "RSHV Ratio"}
# measure -> (national column, median column or None) in the national file
NATIONAL_COLS = {"ASC-9": ("Avg ASC-9 Nat Rate*", "Median ASC-9 Nat Rate*"),
                 "ASC-11": ("Avg ASC-11 Nat Rate*", "Median ASC-11 Nat Rate*"),
                 "ASC-12": ("ASC-12 Nat Rate", None), "ASC-17": ("ASC-17 Nat Rate", None),
                 "ASC-18": ("ASC-18 Nat Rate", None), "ASC-19": ("ASC-19 Nat Ratio", None)}


def _num(col: str) -> str:
    return f"TRY_CAST(NULLIF(NULLIF(TRIM(\"{col}\"), 'N/A'), '') AS DOUBLE)"


def _csv(path: str) -> str:
    return f"read_csv('{path}', all_varchar=true, header=true)"


def build(con, facility: str, national: str, rd: str) -> tuple[str, str]:
    fac_cols = ["Facility ID", "NPI", "State", "Year"] + list(RATES.values())
    for m, score in RISK_ADJUSTED.items():
        fac_cols += [f"{m} Total Cases", f"{m} Performance Category", f"{m} {score}"]
    require_columns(con, facility, fac_cols)
    nat_cols = ["Year"] + [c for pair in NATIONAL_COLS.values() for c in pair if c]
    require_columns(con, national, nat_cols)

    parts = []
    for m, col in RATES.items():
        parts.append(f"""SELECT TRY_CAST(NPI AS BIGINT) AS npi, "Facility ID" AS ccn, '{m}' AS measure, {_num(col)} AS score,
                                NULL::VARCHAR AS compared, NULL::BIGINT AS sample, "Year" AS period
                         FROM {_csv(facility)} WHERE "State" = 'GA'""")
    for m, score in RISK_ADJUSTED.items():
        parts.append(f"""SELECT TRY_CAST(NPI AS BIGINT) AS npi, "Facility ID" AS ccn, '{m}' AS measure, {_num(f"{m} {score}")} AS score,
                                CASE WHEN TRIM("{m} Performance Category") IN ('N/A', '') THEN NULL
                                     ELSE TRIM("{m} Performance Category") END AS compared,
                                TRY_CAST(NULLIF("{m} Total Cases", 'N/A') AS BIGINT) AS sample, "Year" AS period
                         FROM {_csv(facility)} WHERE "State" = 'GA'""")
    con.execute("CREATE TABLE asc_rows AS SELECT * FROM (" + " UNION ALL ".join(f"({p})" for p in parts)
                + ") WHERE score IS NOT NULL OR compared IS NOT NULL")
    if con.execute("SELECT count(*) FROM asc_rows").fetchone()[0] == 0:
        raise SystemExit(f"{facility}: no Georgia ASC rows")
    dup = con.execute("SELECT ccn, npi, measure FROM asc_rows GROUP BY ALL HAVING count(*) > 1 LIMIT 3").fetchall()
    if dup:
        raise SystemExit(f"an ASC repeats a measure, for example {dup}")

    nat = []
    for m, (avg, med) in NATIONAL_COLS.items():
        nat.append(f"""SELECT '{m}' AS measure, {_num(avg)} AS national, {_num(med) if med else 'NULL::DOUBLE'} AS median,
                              "Year" AS period FROM {_csv(national)}""")
    con.execute("CREATE TABLE nat AS " + " UNION ALL ".join(f"({p})" for p in nat))

    out_ga, out_nat = f"{rd}/asc_measures_ga.parquet", f"{rd}/asc_measures_national.parquet"
    write_parquet_atomic(con, "SELECT * FROM asc_rows ORDER BY ccn, npi, measure", out_ga)
    write_parquet_atomic(con, "SELECT * FROM nat ORDER BY measure", out_nat)
    return out_ga, out_nat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None, help="local facility CSV (skips the download)")
    ap.add_argument("--national-file", default=None, help="local national CSV (skips the download)")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    print("→ CMS ASC quality measures")
    facility = fetch_dataset(FACILITY, args.data_dir, args.test, args.file)
    national = fetch_dataset(NATIONAL, args.data_dir, args.test, args.national_file)
    con = duckdb.connect()
    out_ga, out_nat = build(con, facility, national, store_dir(args.data_dir, args.test, "reference"))
    n, a = con.execute("SELECT count(*), count(DISTINCT ccn) FROM asc_rows").fetchone()
    print(f"  wrote {out_ga} ({n:,} rows, {a:,} ASCs)\n  wrote {out_nat}")


if __name__ == "__main__":
    main()
