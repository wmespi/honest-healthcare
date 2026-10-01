#!/usr/bin/env python3
"""
Build `data/reference/zip_centroids.parquet` — ZIP (ZCTA) -> internal-point
lat/lon from the Census Gazetteer, so `/providers/search?zip=` can measure
distance. Public, free, keyless. See reference/zip-centroids.md.

  zip | lat | lon

Usage: python3 -m reference.zip_centroids [--file PATH] [--data-dir data] [--test]
"""
import argparse
import zipfile

import duckdb

from ._common import fetch_to_cache, ref_dir, write_parquet_atomic

URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_zcta_national.zip"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None, help="local Gazetteer .zip or tab-delimited .txt")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    rd = ref_dir(args.data_dir, args.test)
    print("→ Census Gazetteer ZCTA centroids")
    path = fetch_to_cache(f"{rd}/.cache/gaz_zcta.zip", [URL], args.file)
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".txt"))
            txt = f"{rd}/.cache/gaz_zcta.txt"
            with open(txt, "wb") as f:
                f.write(z.read(name))
    else:
        txt = path

    out = f"{rd}/zip_centroids.parquet"
    con = duckdb.connect()
    write_parquet_atomic(con, f"""
        SELECT LPAD(TRIM(GEOID), 5, '0') AS zip,
               TRY_CAST(TRIM(INTPTLAT) AS DOUBLE) AS lat,
               TRY_CAST(TRIM(INTPTLONG) AS DOUBLE) AS lon
        FROM read_csv('{txt}', delim='\t', header=true, all_varchar=true)
        WHERE TRY_CAST(TRIM(INTPTLAT) AS DOUBLE) IS NOT NULL
    """, out)
    print(f"→ wrote {out}\n  {con.execute(f'SELECT count(*) FROM read_parquet({chr(39)}{out}{chr(39)})').fetchone()[0]:,} ZIPs")


if __name__ == "__main__":
    main()
