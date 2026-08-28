#!/usr/bin/env python3
"""
Build data/reference/nucc_taxonomy.parquet — a consumer-readable specialty label
for every NUCC Health Care Provider Taxonomy code, from PUBLIC data:

  NUCC Health Care Provider Taxonomy Code Set (nucc.org, public domain).
  Columns: Code, Grouping, Classification, Specialization, Definition, Notes,
           Display Name, Section

Output columns:
  taxonomy_code | grouping | classification | specialization
  display_name  (NUCC "Display Name", e.g. "Cardiovascular Disease Physician")
  specialty     (clean short label: Specialization, else Classification)
  is_individual (Section == "Individual")

The NPPES GA subset carries `taxonomy_code`; the backend LEFT JOINs this so
provider search / the cost card can show "· Cardiology" instead of the useless
"· Physician (individual)" bucket.

Usage:
  python3 -m reference.taxonomy_labels [--nucc-url URL | --nucc-file PATH]
                                       [--data-dir data] [--test]
"""
import argparse

import duckdb

from ._common import fetch_to_cache, ref_dir, write_parquet_atomic

# 26.1 is the current cut (Jul 2026). NUCC re-stamps the trailing version twice a
# year; the download page is https://nucc.org/index.php/code-sets-mainmenu-41/
NUCC_URLS = [
    "https://nucc.org/images/stories/CSV/nucc_taxonomy_261.csv",
    "https://nucc.org/images/stories/CSV/nucc_taxonomy_251.csv",
    "https://nucc.org/images/stories/CSV/nucc_taxonomy_250.csv",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nucc-url", default=None)
    ap.add_argument("--nucc-file", default=None)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    rd = ref_dir(args.data_dir, args.test)
    cache = f"{rd}/nucc_taxonomy.csv"
    out_path = f"{rd}/nucc_taxonomy.parquet"

    print("→ NUCC taxonomy")
    path = fetch_to_cache(
        cache,
        [args.nucc_url] if args.nucc_url else NUCC_URLS,
        args.nucc_file,
        header_check=b"Code,Grouping,Classification",
    )

    con = duckdb.connect()
    write_parquet_atomic(
        con,
        f"""
          SELECT
            "Code"                               AS taxonomy_code,
            NULLIF("Grouping", '')               AS grouping,
            NULLIF("Classification", '')          AS classification,
            NULLIF("Specialization", '')          AS specialization,
            NULLIF("Display Name", '')            AS display_name,
            COALESCE(
              NULLIF("Specialization", ''),
              NULLIF("Classification", ''),
              NULLIF("Display Name", '')
            )                                     AS specialty,
            ("Section" = 'Individual')            AS is_individual
          FROM read_csv_auto('{path}', header=true, all_varchar=true)
          WHERE "Code" IS NOT NULL AND "Code" != ''
        """,
        out_path,
    )
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0]
    print(f"→ wrote {out_path} — {n:,} taxonomy codes")
    sample = con.execute(
        f"""SELECT specialty, count(*) n FROM read_parquet('{out_path}')
            GROUP BY 1 ORDER BY n DESC LIMIT 10"""
    ).fetchall()
    for s, c in sample:
        print(f"    {c:>4}  {s}")


if __name__ == "__main__":
    main()
