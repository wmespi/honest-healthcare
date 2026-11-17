#!/usr/bin/env python3
"""
Build the hospital quality measures from four CMS Provider Data Catalog datasets:

  data/ingestion/reference/hospital_measures_ga.parquet        one row per Georgia hospital and measure
      ccn | measure | score | compared | sample | start_date | end_date
  data/ingestion/reference/hospital_measures_national.parquet  one row per measure CMS publishes a national value for
      measure | score | start_date | end_date

Sources and the measures kept from each:
  632h-zaca Unplanned Hospital Visits    OP-32, OP-36        (its own "Compared to National" label)
  yv7e-xc69 Timely and Effective Care    OP-29, OP-31        (national: isrn-hqyy)
  wkfw-kthe Outpatient Imaging Efficiency OP-10, OP-39       (national: di9i-zzrc)
  dgck-syfz HCAHPS                       H_STAR_RATING only  (national file has no summary star)

`score` is null where CMS publishes "Not Available" or "Not Applicable". See
ingestion/reference/hospital-measures.md.

Usage: python3 -m ingestion.reference.hospital_measures [--file DATASET_ID=CSV ...] [--data-dir data] [--test]
"""
import argparse

import duckdb

from ._common import store_dir, write_parquet_atomic
from ._pdc import fetch_dataset, require_columns

UNPLANNED, TIMELY, IMAGING, HCAHPS = "632h-zaca", "yv7e-xc69", "wkfw-kthe", "dgck-syfz"
TIMELY_NAT, IMAGING_NAT = "isrn-hqyy", "di9i-zzrc"

OP_MEASURES = {UNPLANNED: ("OP_32", "OP_36"), TIMELY: ("OP_29", "OP_31"), IMAGING: ("OP-10", "OP-39")}  # as each file spells them
NATIONAL_MEASURES = ("OP_29", "OP_31", "OP_10", "OP_39")

COLUMNS = {
    UNPLANNED: ["Facility ID", "State", "Measure ID", "Compared to National", "Denominator", "Score", "Start Date", "End Date"],
    TIMELY: ["Facility ID", "State", "Measure ID", "Score", "Sample", "Start Date", "End Date"],
    IMAGING: ["Facility ID", "State", "Measure ID", "Score", "Start Date", "End Date"],
    HCAHPS: ["Facility ID", "State", "HCAHPS Measure ID", "Patient Survey Star Rating", "Number of Completed Surveys",
             "Start Date", "End Date"],
    TIMELY_NAT: ["Measure ID", "Score", "Start Date", "End Date"],
    IMAGING_NAT: ["Measure ID", "Score", "Start Date", "End Date"],
}


def _q(name: str) -> str:
    return f'"{name}"'


def _num(name: str) -> str:
    return f"TRY_CAST(NULLIF(TRIM({_q(name)}), '') AS DOUBLE)"


def _measure(name: str) -> str:
    """CMS writes OP_32 in some files and OP-10 in others; one spelling, with a hyphen."""
    return f"REPLACE(TRIM({_q(name)}), '_', '-')"


DATES = ('TRY_STRPTIME("Start Date", \'%m/%d/%Y\')::DATE AS start_date, '
         'TRY_STRPTIME("End Date", \'%m/%d/%Y\')::DATE AS end_date')


def _csv(path: str) -> str:
    return f"read_csv('{path}', all_varchar=true, header=true)"


def build(con, paths: dict, rd: str) -> tuple[str, str]:
    for ds, cols in COLUMNS.items():
        require_columns(con, paths[ds], cols)

    def ga(ds, measures, sel):
        ids = ", ".join(f"'{m}'" for m in measures)
        return f"""SELECT {sel} FROM {_csv(paths[ds])} WHERE "State" = 'GA' AND TRIM("Measure ID") IN ({ids})"""

    parts = [
        ga(UNPLANNED, OP_MEASURES[UNPLANNED],
           f""" "Facility ID" AS ccn, {_measure("Measure ID")} AS measure, {_num("Score")} AS score,
               CASE WHEN "Compared to National" IN ('Not Available', '') THEN NULL ELSE TRIM("Compared to National") END AS compared,
               TRY_CAST("Denominator" AS BIGINT) AS sample, {DATES}"""),
        ga(TIMELY, OP_MEASURES[TIMELY],
           f""" "Facility ID" AS ccn, {_measure("Measure ID")} AS measure, {_num("Score")} AS score,
               NULL AS compared, TRY_CAST("Sample" AS BIGINT) AS sample, {DATES}"""),
        ga(IMAGING, OP_MEASURES[IMAGING],
           f""" "Facility ID" AS ccn, {_measure("Measure ID")} AS measure, {_num("Score")} AS score,
               NULL AS compared, NULL AS sample, {DATES}"""),
        f"""SELECT "Facility ID" AS ccn, 'H_STAR_RATING' AS measure, {_num("Patient Survey Star Rating")} AS score,
                   NULL AS compared, TRY_CAST("Number of Completed Surveys" AS BIGINT) AS sample, {DATES}
            FROM {_csv(paths[HCAHPS])}
            WHERE "State" = 'GA' AND TRIM("HCAHPS Measure ID") = 'H_STAR_RATING'""",
    ]
    con.execute("CREATE TABLE ga AS " + " UNION ALL ".join(f"({p})" for p in parts))

    dup = con.execute("SELECT ccn, measure FROM ga GROUP BY ALL HAVING count(*) > 1 LIMIT 3").fetchall()
    if dup:
        raise SystemExit(f"a hospital repeats a measure, for example {dup}")
    for m in [m.replace("_", "-") for ms in OP_MEASURES.values() for m in ms] + ["H_STAR_RATING"]:
        if con.execute("SELECT count(*) FROM ga WHERE measure = ?", [m]).fetchone()[0] == 0:
            raise SystemExit(f"no Georgia rows for {m}: CMS changed the measure id")

    nat = " UNION ALL ".join(
        f"""(SELECT {_measure("Measure ID")} AS measure, {_num("Score")} AS score, {DATES}
             FROM {_csv(paths[ds])} WHERE TRIM("Measure ID") IN ({", ".join(f"'{m}'" for m in NATIONAL_MEASURES)}))"""
        for ds in (TIMELY_NAT, IMAGING_NAT))
    con.execute("CREATE TABLE nat AS " + nat)
    if con.execute("SELECT count(DISTINCT measure) FROM nat").fetchone()[0] != len(NATIONAL_MEASURES):
        raise SystemExit("a national value is missing for one of " + ", ".join(NATIONAL_MEASURES))

    out_ga, out_nat = f"{rd}/hospital_measures_ga.parquet", f"{rd}/hospital_measures_national.parquet"
    write_parquet_atomic(con, "SELECT * FROM ga ORDER BY ccn, measure", out_ga)
    write_parquet_atomic(con, "SELECT * FROM nat ORDER BY measure", out_nat)
    return out_ga, out_nat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", default=[], metavar="DATASET_ID=CSV",
                    help="local CSV for one dataset id (repeatable); the rest download")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()
    local = dict(f.split("=", 1) for f in args.file)

    print("→ CMS hospital quality measures")
    paths = {ds: fetch_dataset(ds, args.data_dir, args.test, local.get(ds)) for ds in COLUMNS}
    con = duckdb.connect()
    out_ga, out_nat = build(con, paths, store_dir(args.data_dir, args.test, "reference"))
    n, h = con.execute("SELECT count(*), count(DISTINCT ccn) FROM ga").fetchone()
    print(f"  wrote {out_ga} ({n:,} rows, {h:,} hospitals)\n  wrote {out_nat}")


if __name__ == "__main__":
    main()
