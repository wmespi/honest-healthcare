#!/usr/bin/env python3
"""
Build data/ingestion/reference/clinician_volume_ga.parquet from CMS's Doctors and Clinicians
"Utilization Data" (n0yb-util), the procedure volumes shown on clinician profile pages:

  npi | category | count | percentile | displayed | published

The file is national with no state column, so it is kept to the NPIs in
`data/ingestion/nppes/ga_providers.parquet`. `category` is CMS's procedure category text.
`count` is CMS's reported value as text, either a number ("57") or a band ("1-10").
`percentile` is CMS's, null where it publishes none. The file carries no period of its
own, so `published` is the catalog's release date for the file. `displayed` is
whether CMS shows the row on the clinician's profile (`Profile_Display_Indicator` = Y).
See ingestion/reference/clinician-volume.md.

Usage: python3 -m ingestion.reference.clinician_volume [--file CSV --published YYYY-MM-DD] [--data-dir data] [--test]
"""
import argparse

import duckdb

from ._common import store_dir, write_parquet_atomic
from ._pdc import fetch_dataset, metadata, require_columns

DATASET = "n0yb-util"
COLUMNS = ["NPI", "Procedure_Category", "Count", "Percentile", "Profile_Display_Indicator"]


def build(con, src: str, nppes_path: str, published: str | None, out_path: str) -> None:
    require_columns(con, src, COLUMNS)
    write_parquet_atomic(con, f"""
        SELECT TRY_CAST(TRIM(NPI) AS BIGINT) AS npi,
               TRIM(Procedure_Category) AS category,
               TRIM("Count") AS count,
               TRY_CAST(NULLIF(TRIM(Percentile), '') AS INTEGER) AS percentile,
               TRIM(Profile_Display_Indicator) = 'Y' AS displayed,
               TRY_CAST({"NULL" if published is None else f"'{published}'"} AS DATE) AS published
        FROM read_csv('{src}', all_varchar=true, header=true)
        WHERE TRY_CAST(TRIM(NPI) AS BIGINT) IN (SELECT npi FROM read_parquet('{nppes_path}'))
          AND TRIM(Procedure_Category) <> '' AND TRIM("Count") <> ''
        ORDER BY npi, category
    """, out_path)
    if con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0] == 0:
        raise SystemExit(f"{src}: no Georgia clinicians")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None, help="local CSV (skips the download)")
    ap.add_argument("--published", default=None, help="release date of a local --file")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    print("→ CMS Doctors and Clinicians utilization")
    published = args.published or (None if args.file else metadata(DATASET).get("released"))
    src = fetch_dataset(DATASET, args.data_dir, args.test, args.file)
    out = f"{store_dir(args.data_dir, args.test, 'reference')}/clinician_volume_ga.parquet"
    con = duckdb.connect()
    build(con, src, f"{store_dir(args.data_dir, args.test, 'nppes')}/ga_providers.parquet", published, out)
    n, c, k = con.execute(f"SELECT count(*), count(DISTINCT npi), count(DISTINCT category) FROM read_parquet('{out}')").fetchone()
    print(f"  wrote {out} ({n:,} rows, {c:,} clinicians, {k:,} categories)")


if __name__ == "__main__":
    main()
