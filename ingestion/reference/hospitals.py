#!/usr/bin/env python3
"""
Build data/ingestion/reference/hospitals_ga.parquet: one row per Georgia hospital, from CMS's
Hospital General Information (Care Compare).

  https://data.cms.gov/provider-data/dataset/xubh-q36u

Why: a hospital is a place on the find-care screen, keyed by its CCN, and this
is the public register of hospitals with each one's name, address, phone and
Medicare star rating. The insurer files only name billing NPIs, and NPPES has
no star rating.

Rows kept: Georgia (`State = 'GA'`). `stars` is the overall rating, 1 to 5, and
null where CMS publishes "Not Available".

Usage:
  python3 -m ingestion.reference.hospitals [--url CSV_URL | --file CSV] [--data-dir data] [--test]
"""
import argparse
import json
import urllib.request

import duckdb

from ._common import fetch_to_cache, raw_dir, store_dir, write_parquet_atomic

DATASET = "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/xubh-q36u"


def _csv_url() -> str:
    """The dataset's current CSV. CMS re-stamps the file's path on every publish,
    so it's read from the dataset's metadata rather than kept here."""
    with urllib.request.urlopen(DATASET, timeout=60) as r:
        meta = json.load(r)
    for dist in meta.get("distribution", []):
        url = dist.get("downloadURL") or ""
        if url.lower().endswith(".csv"):
            print(f"  {meta.get('title')}, modified {meta.get('modified')}")
            return url
    raise SystemExit(f"{DATASET}: no CSV distribution")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None, help="override the CSV URL")
    ap.add_argument("--file", default=None, help="local CSV (skips the download)")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    rd = store_dir(args.data_dir, args.test, "reference")
    if args.file:
        path = args.file
    else:
        url = args.url or _csv_url()
        path = fetch_to_cache(f"{raw_dir(args.data_dir, args.test, 'downloads')}/{url.rsplit('/', 2)[-2]}_{url.rsplit('/', 1)[-1]}", [url],
                              header_check=b'"Facility ID"')
    print(f"→ CMS Hospital General Information: {path}")

    con = duckdb.connect()
    con.execute(f"""
        CREATE TABLE hospitals AS
        SELECT "Facility ID" AS ccn,
               "Facility Name" AS name,
               "Address" AS address,
               "City/Town" AS city,
               "State" AS state,
               "ZIP Code" AS postal_code,
               "County/Parish" AS county,
               NULLIF("Telephone Number", '') AS phone,
               "Hospital Type" AS hospital_type,
               "Hospital Ownership" AS ownership,
               "Emergency Services" = 'Yes' AS emergency_services,
               TRY_CAST("Hospital overall rating" AS INTEGER) AS stars
        FROM read_csv('{path}', all_varchar=true, header=true)
        WHERE "State" = 'GA'
    """)
    dup = con.execute("SELECT ccn FROM hospitals GROUP BY ccn HAVING count(*) > 1 LIMIT 3").fetchall()
    if dup:
        raise SystemExit(f"a CCN repeats, for example {dup}")
    bad = con.execute("SELECT ccn, stars FROM hospitals WHERE stars NOT BETWEEN 1 AND 5 LIMIT 3").fetchall()
    if bad:
        raise SystemExit(f"a star rating outside 1 to 5, for example {bad}")
    if con.execute("SELECT count(*) FROM hospitals").fetchone()[0] == 0:
        raise SystemExit(f"{path}: no Georgia hospitals")

    out = f"{rd}/hospitals_ga.parquet"
    write_parquet_atomic(con, "SELECT * FROM hospitals ORDER BY ccn", out)
    n, rated = con.execute("SELECT count(*), count(stars) FROM hospitals").fetchone()
    print(f"  wrote {out} ({n:,} hospitals, {rated:,} with a star rating)")


if __name__ == "__main__":
    main()
