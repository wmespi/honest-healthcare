#!/usr/bin/env python3
"""
Build data/ingestion/reference/shoppable_services.parquet - CMS's 500 shoppable items and
services, each with CMS's own classification of the code (RBCS).

  Shoppable items and services (Transparency in Coverage rule)
  https://www.cms.gov/files/document/transparency-coverage-500-items-services-508.pdf
  Restructured BETOS Classification System (RBCS)
  https://data.cms.gov/provider-summary-by-type-of-service/provider-service-classifications/restructured-betos-classification-system

Why: the list of codes people can shop for is the candidate set for the services the
find-care screen offers, and RBCS says what kind of care each code is. CMS publishes the
list only as a PDF, whose descriptions are the AMA's, so only the codes are checked in
(`ingestion/reference/shoppable_items.csv`).

One row per code in that file, sorted by `billing_code_type, billing_code`. The RBCS
columns are null where the code has no RBCS row (MS-DRG codes, and any code RBCS does not
cover). RBCS keeps every assignment a code ever had; the row used is the code's latest
assignment, and if several remain, the one with the latest end date.

Usage:
  python3 -m ingestion.reference.shoppable [--url CSV_URL] [--rbcs-file RBCS.csv]
                            [--items-file CSV] [--data-dir data] [--test]
"""
import argparse
import json
import os
import sys
import urllib.request

import duckdb

from ._common import fetch_to_cache, raw_dir, store_dir, write_parquet_atomic

CMS_DATA_JSON = "https://data.cms.gov/data.json"
# The catalog title carries a date suffix ("... : 2025-01-02"), so match on the start.
CMS_TITLE = "Restructured BETOS Classification System"
# Used if the data.json lookup fails; pass --url to override.
RBCS_URL_FALLBACK = (
    "https://data.cms.gov/sites/default/files/2026-05/"
    "37fde1d7-b350-4f22-87f0-07ca84402e44/RBCS%20Taxonomy_RY2025.csv"
)
ITEMS_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shoppable_items.csv")
USER_AGENT = {"User-Agent": "honest-healthcare-etl"}

NEEDED = ["HCPCS_Cd", "RBCS_Cat_Desc", "RBCS_Subcat_Desc", "RBCS_Family_Desc",
          "RBCS_Latest_Assignment", "HCPCS_Cd_End_Dt"]
MIN_MATCH = 0.95


def resolve_url() -> str:
    """The RBCS taxonomy CSV, found by dataset title in data.cms.gov/data.json."""
    try:
        req = urllib.request.Request(CMS_DATA_JSON, headers=USER_AGENT)
        with urllib.request.urlopen(req, timeout=60) as r:
            catalog = json.load(r)
        urls = []
        for ds in catalog.get("dataset", []):
            if not ds.get("title", "").strip().startswith(CMS_TITLE):
                continue
            for dist in ds.get("distribution", []):
                url = dist.get("downloadURL") or ""
                if dist.get("mediaType") == "text/csv" and url.lower().endswith(".csv"):
                    urls.append(url)
        if urls:
            # The path starts with the publish month (/2026-09/), so the newest sorts last.
            return sorted(urls)[-1]
    except Exception as e:  # noqa: BLE001
        print(f"  (data.json lookup failed: {e} - using fallback URL)", file=sys.stderr)
    return RBCS_URL_FALLBACK


def build(con, items_file: str, rbcs_file: str, out: str) -> int:
    """Write the parquet from the two CSVs and return its row count."""
    cols = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_csv('{rbcs_file}', all_varchar=true)").fetchall()]
    missing = [c for c in NEEDED if c not in cols]
    if missing:
        raise SystemExit(f"{rbcs_file}: missing column(s) {missing} - CMS changed the RBCS layout; "
                         f"update NEEDED in ingestion/reference/shoppable.py")

    repeated = [code for (code,) in con.execute(
        f"SELECT billing_code FROM read_csv('{items_file}', all_varchar=true) "
        f"GROUP BY 1 HAVING count(*) > 1 ORDER BY 1 LIMIT 5").fetchall()]
    if repeated:
        raise SystemExit(f"{items_file}: a code repeats ({', '.join(repeated)}) - remove the duplicate rows")

    sel = f"""
        WITH rbcs AS (
            SELECT trim(HCPCS_Cd) AS code, RBCS_Cat_Desc AS cat, RBCS_Subcat_Desc AS subcat,
                   RBCS_Family_Desc AS fam,
                   row_number() OVER (
                       PARTITION BY trim(HCPCS_Cd)
                       ORDER BY try_strptime(HCPCS_Cd_End_Dt, '%m/%d/%Y') DESC NULLS LAST,
                                RBCS_Cat_Desc, RBCS_Subcat_Desc, RBCS_Family_Desc) AS rn
            FROM read_csv('{rbcs_file}', all_varchar=true)
            WHERE RBCS_Latest_Assignment = '1'
        )
        SELECT i.billing_code::VARCHAR AS billing_code,
               i.billing_code_type::VARCHAR AS billing_code_type,
               r.cat::VARCHAR AS rbcs_category,
               r.subcat::VARCHAR AS rbcs_subcategory,
               r.fam::VARCHAR AS rbcs_family
        FROM read_csv('{items_file}', all_varchar=true) i
        LEFT JOIN rbcs r ON r.code = i.billing_code AND r.rn = 1
        ORDER BY i.billing_code_type, i.billing_code
    """
    write_parquet_atomic(con, sel, out)
    total, cpt_hcpcs, matched = con.execute(f"""
        SELECT count(*),
               count(*) FILTER (WHERE billing_code_type IN ('CPT', 'HCPCS')),
               count(*) FILTER (WHERE billing_code_type IN ('CPT', 'HCPCS') AND rbcs_category IS NOT NULL)
        FROM read_parquet('{out}')""").fetchone()
    if cpt_hcpcs and matched / cpt_hcpcs < MIN_MATCH:
        os.remove(out)
        raise SystemExit(f"only {matched} of {cpt_hcpcs} CPT/HCPCS codes have an RBCS row "
                         f"(need {MIN_MATCH:.0%}) - the RBCS layout or code format probably "
                         f"changed; look at {rbcs_file}")
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None, help="override the RBCS taxonomy CSV URL")
    ap.add_argument("--rbcs-file", default=None, help="local RBCS CSV (skips the download)")
    ap.add_argument("--items-file", default=ITEMS_DEFAULT, help="the shoppable codes CSV")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    rd = store_dir(args.data_dir, args.test, "reference")
    downloads = raw_dir(args.data_dir, args.test, "downloads")
    if args.rbcs_file:
        rbcs = args.rbcs_file
    else:
        url = args.url or resolve_url()
        # The file name from the URL is in the cache name, so a new year is fetched afresh.
        name = os.path.basename(url.split("?")[0]).replace("%20", "_")
        rbcs = fetch_to_cache(f"{downloads}/rbcs_{name}", [url], headers=USER_AGENT,
                              header_check=b"HCPCS_Cd")
    print(f"→ CMS shoppable items + RBCS: {rbcs}")

    con = duckdb.connect()
    con.execute("SET temp_directory='/tmp/dsp'")
    out = f"{rd}/shoppable_services.parquet"
    n = build(con, args.items_file, rbcs, out)
    print(f"  wrote {out} ({n:,} rows)")


if __name__ == "__main__":
    main()
