#!/usr/bin/env python3
"""
Build data/reference/opps.parquet — the Medicare hospital-outpatient (OPPS)
national payment rate per HCPCS/CPT code, from CMS's quarterly Addendum B.

  "Quarterly Addenda Updates" (Hospital Outpatient PPS)
  https://www.cms.gov/medicare/payment/prospective-payment-systems/hospital-outpatient-pps/quarterly-addenda-updates

Why: Anthem's file carries no hospital facility price for imaging, so the app
shows this Medicare rate as a labelled estimate of the hospital's part of the
scan. The build reduces it to `code_dim.opps_payment`.

Rows kept: codes with a payment rate (status indicators that pay under OPPS).
Codes paid elsewhere (status `A`, the physician fee schedule) or not payable
carry no rate and are dropped. The rate is the national unadjusted amount; the
local wage index is not applied.

Usage:
  python3 -m reference.opps [--cms-url ZIP_URL] [--addendum-file B.txt]
                            [--data-dir data] [--test]
"""
import argparse
import re
import zipfile

import duckdb

from ._common import fetch_to_cache, ref_dir, write_parquet_atomic

# The quarterly release is re-stamped each quarter; pass --cms-url to override.
CMS_ZIP_FALLBACK = "https://www.cms.gov/files/zip/October-2026-OPPS-Addendum-B.zip"


def _extract_txt(zip_path: str, dest_dir: str) -> str:
    with zipfile.ZipFile(zip_path) as z:
        members = [n for n in z.namelist() if n.lower().endswith(".txt")]
        if not members:
            raise SystemExit(f"{zip_path}: no .txt addendum inside (found {z.namelist()})")
        target = f"{dest_dir}/{members[0].split('/')[-1]}"
        with z.open(members[0]) as src, open(target, "wb") as dst:
            dst.write(src.read())
    return target


def _preamble(path: str) -> tuple[int, int]:
    """(0-based index of the header row, calendar year from the title row)."""
    year = 0
    with open(path, encoding="latin-1") as f:
        for i, line in enumerate(f):
            if i > 40:
                break
            m = re.search(r"for CY (\d{4})", line)
            if m:
                year = int(m.group(1))
            if line.split("\t", 1)[0].strip().lower() == "hcpcs code":
                return i, year
    raise SystemExit(f"{path}: no 'HCPCS Code' header row")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cms-url", default=None, help="override the Addendum B zip URL")
    ap.add_argument("--addendum-file", default=None, help="local Addendum B .txt (skips the download)")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    rd = ref_dir(args.data_dir, args.test)
    url = args.cms_url or CMS_ZIP_FALLBACK
    if args.addendum_file:
        txt = args.addendum_file
    else:
        zip_path = fetch_to_cache(f"{rd}/.cache/opps_addendum_b.zip", [url], header_check=b"PK")
        txt = _extract_txt(zip_path, f"{rd}/.cache")
    header, year = _preamble(txt)
    print(f"→ CMS OPPS Addendum B (CY {year or '?'}): {txt}")

    con = duckdb.connect()
    sel = f"""
        SELECT trim(c0) AS billing_code,
               CASE WHEN regexp_full_match(trim(c0), '[0-9]{{5}}') THEN 'CPT' ELSE 'HCPCS' END
                   AS billing_code_type,
               trim(c2) AS status_indicator,
               NULLIF(trim(c3), '') AS apc,
               CAST(replace(replace(trim(c5), '$', ''), ',', '') AS DOUBLE) AS payment_rate,
               {year} AS year
        FROM read_csv('{txt}', delim='\t', header=false, skip={header + 1}, all_varchar=true,
                      null_padding=true, strict_mode=false, ignore_errors=true,
                      names=['c0','c1','c2','c3','c4','c5','c6','c7','c8','c9','c10','c11','c12'])
        WHERE regexp_full_match(trim(c0), '[0-9A-Z]{{5}}')
          AND regexp_full_match(replace(replace(trim(c5), '$', ''), ',', ''), '[0-9]+(\\.[0-9]+)?')
    """
    out = f"{rd}/opps.parquet"
    write_parquet_atomic(con, sel, out)
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    print(f"  wrote {out} ({n:,} rows)")


if __name__ == "__main__":
    main()
