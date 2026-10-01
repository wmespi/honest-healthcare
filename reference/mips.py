#!/usr/bin/env python3
"""
Build `data/reference/mips_ga.parquet` — one CMS MIPS overall score per Georgia
clinician NPI, from the Provider Data Catalog's latest
"PY <year> Clinician Public Reporting: Overall MIPS Performance" file.

  npi | mips_score | mips_source | performance_year

A clinician can appear under several `source`s (individual, group, apm, ...);
the kept row prefers an individual score, then the most specific group-level one
(the score CMS attributes to that clinician). A score of 0 is CMS's published
value and is kept as-is. Scoped to the NPIs in `data/nppes/ga_providers.parquet`.
See reference/mips.md.

Usage: python3 -m reference.mips [--url URL | --file PATH] [--data-dir data] [--test]
"""
import argparse
import re

import duckdb

from ._common import fetch_to_cache_streaming, nppes_dir, ref_dir, write_parquet_atomic
from .doctors_clinicians import PDC_METASTORE  # noqa: F401  (same catalog)

TITLE_RE = re.compile(r"^PY (\d{4}) Clinician Public Reporting: Overall MIPS Performance$")
SOURCE_PRIORITY = ["individual", "subgroup", "group", "virtual group", "apm"]


def resolve_latest():
    """(performance_year, csv_url) of the newest published year, or exit."""
    import json
    import urllib.request
    with urllib.request.urlopen(PDC_METASTORE, timeout=60) as r:
        items = json.load(r)
    found = []
    for ds in items:
        m = TITLE_RE.match((ds.get("title") or "").strip())
        if not m:
            continue
        for dist in ds.get("distribution", []):
            url = (dist.get("data", dist).get("downloadURL") or "")
            if url.lower().endswith(".csv"):
                found.append((int(m.group(1)), url))
    if not found:
        raise SystemExit("no 'Clinician Public Reporting: Overall MIPS Performance' dataset in the catalog; pass --url")
    return max(found)


def build(con, src: str, out_path: str, nppes_path: str, year: int) -> None:
    prio = " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(SOURCE_PRIORITY))
    select_sql = f"""
        WITH src AS (
            SELECT TRY_CAST(TRIM(npi) AS BIGINT) AS npi,
                   TRY_CAST(NULLIF(TRIM(final_mips_score), '') AS DOUBLE) AS mips_score,
                   LOWER(TRIM(_source)) AS mips_source
            FROM read_csv('{src}', header=true, all_varchar=true, normalize_names=true,
                          quote='"', escape='"', ignore_errors=true)
        )
        SELECT npi, mips_score, mips_source, {year}::INTEGER AS performance_year
        FROM src
        WHERE npi IN (SELECT npi FROM read_parquet('{nppes_path}'))
          AND mips_score IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY npi
            ORDER BY CASE mips_source {prio} ELSE {len(SOURCE_PRIORITY)} END, mips_score DESC
        ) = 1
    """
    write_parquet_atomic(con, select_sql, out_path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None)
    ap.add_argument("--file", default=None, help="local CSV instead of downloading")
    ap.add_argument("--year", type=int, default=None, help="performance year (required with --file/--url)")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    rd = ref_dir(args.data_dir, args.test)
    year, url = args.year, args.url
    if not args.file and not url:
        year, url = resolve_latest()
    if year is None:
        raise SystemExit("--year is required with --file / --url")
    print(f"→ CMS MIPS overall performance, PY {year}")
    src = fetch_to_cache_streaming(f"{rd}/.cache/mips_{year}.csv", [url] if url else [], args.file)

    out = f"{rd}/mips_ga.parquet"
    con = duckdb.connect()
    build(con, src, out, f"{nppes_dir(args.data_dir, args.test)}/ga_providers.parquet", year)
    n, ind = con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE mips_source='individual') FROM read_parquet('{out}')").fetchone()
    print(f"→ wrote {out}\n  {n:,} GA clinicians with a score ({ind:,} individual-level)")


if __name__ == "__main__":
    main()
