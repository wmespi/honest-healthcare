#!/usr/bin/env python3
"""
Build data/cms/ga_provider_service.parquet — one row per
(Georgia NPI × HCPCS × place-of-service) that was actually billed to Medicare
Part B, from the public CMS dataset:

  "Medicare Physician & Other Practitioners — by Provider and Service"
  https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners

This is the evidence layer behind issue #14: the MRF is a *rate sheet*, it can't
say whether an NPI has ever performed a code. This file can — for Medicare Part B.
`did_bill(npi, code)` in the serving layer reads it.

Why Python/DuckDB and not Go (the source is ~3 GB): the work is a filter +
projection over a CSV with quoted free-text fields, not a streaming parse of
something that can't fit in memory (cf. NPPES at ~9 GB). DuckDB's parallel C++
CSV reader scans all cores, pushes the state filter down, and streams to Parquet
out-of-core — faster than a hand-rolled single-threaded Go csv reader and a
fraction of the code. See ../AGENTS.md#the-language-principle.

Output columns (data/cms/ga_provider_service.parquet):
  npi | hcpcs_cd | place_of_service ('F' facility / 'O' office)
  tot_benes | tot_srvcs | tot_bene_day_srvcs
  avg_mdcr_alowd_amt        (avg Medicare allowed $ — a cross-check vs the MRF rate)
  provider_type             (Medicare's rendering-provider specialty)
  hcpcs_drug_ind            ('Y' = Part B drug / J-code)
  year                      (service year)

Caveats (documented in reference/cms-utilization.md, docs/known-gaps.md):
  - Medicare Part B only — misses pediatric, pure-commercial, cash-only practice.
    Absence != "never does it"; presence is strong.
  - Records derived from <= 10 beneficiaries are EXCLUDED entirely, so a missing
    (npi, code) row is doubly weak evidence.
  - ~2-year lag. Type-2 (facility) NPIs mostly absent — this is a practitioner signal.

Usage:
  python3 -m reference.cms_utilization [--cms-url URL | --cms-file PATH]
                                       [--year N] [--data-dir data] [--test]
"""
import argparse
import json
import re
import sys
import urllib.request

import duckdb

from ._common import fetch_to_cache_streaming, write_parquet_atomic

CMS_DATA_JSON = "https://data.cms.gov/data.json"
CMS_TITLE = "Medicare Physician & Other Practitioners - by Provider and Service"
# Fallback if the data.json lookup fails: 2024 service year, published 2026-05.
# CMS re-stamps the path and bumps D<YY> each year — pass --cms-url to override.
CMS_URL_FALLBACK = (
    "https://data.cms.gov/sites/default/files/2026-05/"
    "b5ebab5a-f490-418a-9bce-4b9f31419356/PHY_R26_P05_V10_D24_Prov_Svc.csv"
)

STATE = "GA"


def _year_from_url(url: str) -> int:
    """PHY_..._D24_Prov_Svc.csv -> 2024. 0 if not found."""
    m = re.search(r"_D(\d{2})_Prov_Svc\.csv", url)
    return 2000 + int(m.group(1)) if m else 0


def resolve_cms_url() -> str:
    """Newest by-Provider-and-Service CSV, from data.cms.gov/data.json."""
    try:
        with urllib.request.urlopen(CMS_DATA_JSON, timeout=30) as r:
            catalog = json.load(r)
        best, best_year = None, -1
        for ds in catalog.get("dataset", []):
            if ds.get("title", "").strip() != CMS_TITLE:
                continue
            for dist in ds.get("distribution", []):
                url = dist.get("downloadURL") or ""
                if dist.get("mediaType") == "text/csv" and url.endswith("_Prov_Svc.csv"):
                    y = _year_from_url(url)
                    if y > best_year:
                        best, best_year = url, y
        if best:
            return best
    except Exception as e:  # noqa: BLE001
        print(f"  (data.json lookup failed: {e} — using fallback URL)", file=sys.stderr)
    return CMS_URL_FALLBACK


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cms-url", default=None, help="override the source CSV URL")
    ap.add_argument("--cms-file", default=None, help="use a local CSV instead of downloading")
    ap.add_argument("--year", type=int, default=None,
                    help="service year to stamp (default: inferred from the URL)")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    data_dir = "data-test" if args.test else args.data_dir
    cms_dir = f"{data_dir}/cms"
    out_path = f"{cms_dir}/ga_provider_service.parquet"

    url = args.cms_url or (None if args.cms_file else resolve_cms_url())
    year = args.year or _year_from_url(url or args.cms_file or "") or 0
    # Year in the cache name so next year's run doesn't reuse this year's CSV.
    cache = f"{cms_dir}/.cache/prov_svc_d{year or 'x'}.csv"

    print(f"→ CMS Physician & Other Practitioners — by Provider and Service"
          f"{f' (service year {year})' if year else ''}")
    src = fetch_to_cache_streaming(cache, [url] if url else [], args.cms_file)

    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")

    # DuckDB's CSV reader is parallel + vectorized and handles the quoted
    # free-text fields (HCPCS_Desc, RUCA_Desc) correctly. all_varchar on read,
    # then cast in the projection so a stray value can't abort the scan.
    src_sql = (
        f"read_csv('{src}', header=true, all_varchar=true, "
        f"quote='\"', escape='\"', sample_size=-1)"
    )

    total = con.execute(f"SELECT count(*) FROM {src_sql}").fetchone()[0]
    print(f"  {total:,} rows in source")

    select_sql = f"""
        SELECT
            TRY_CAST(Rndrng_NPI AS BIGINT)              AS npi,
            HCPCS_Cd                                    AS hcpcs_cd,
            Place_Of_Srvc                               AS place_of_service,
            TRY_CAST(Tot_Benes AS INTEGER)             AS tot_benes,
            TRY_CAST(Tot_Srvcs AS DOUBLE)              AS tot_srvcs,
            TRY_CAST(Tot_Bene_Day_Srvcs AS INTEGER)   AS tot_bene_day_srvcs,
            TRY_CAST(Avg_Mdcr_Alowd_Amt AS DOUBLE)    AS avg_mdcr_alowd_amt,
            NULLIF(Rndrng_Prvdr_Type, '')              AS provider_type,
            NULLIF(HCPCS_Drug_Ind, '')                 AS hcpcs_drug_ind,
            {year} AS year
        FROM {src_sql}
        WHERE Rndrng_Prvdr_State_Abrvtn = '{STATE}'
          AND TRY_CAST(Rndrng_NPI AS BIGINT) IS NOT NULL
    """
    write_parquet_atomic(con, select_sql, out_path)

    kept, npis, codes, pairs = con.execute(f"""
        SELECT count(*), count(DISTINCT npi), count(DISTINCT hcpcs_cd),
               count(DISTINCT (npi, hcpcs_cd))
        FROM read_parquet('{out_path}')
    """).fetchone()
    print(f"→ wrote {out_path}")
    print(f"  {kept:,} GA rows · {npis:,} NPIs · {codes:,} HCPCS codes · "
          f"{pairs:,} (npi, code) pairs")

    top = con.execute(f"""
        SELECT COALESCE(provider_type, '(none)') t, count(*) n
        FROM read_parquet('{out_path}') GROUP BY 1 ORDER BY n DESC LIMIT 10
    """).fetchall()
    for t, n in top:
        print(f"    {n:>7,}  {t}")


if __name__ == "__main__":
    main()
