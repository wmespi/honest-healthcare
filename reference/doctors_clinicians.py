#!/usr/bin/env python3
"""
Build the CMS **Doctors and Clinicians** (Care Compare / Provider Data Catalog)
reference tables — a real practice identity + a hospital-affiliation bridge that
Anthem's coarse `provider_references` buckets can't give us (issue #62).

Two outputs under ``data/reference/``:

  dac_ga.parquet                  one row per NPI
    npi | last_name | first_name | credential | primary_specialty
    org_pac_id | org_name | grad_year | med_school | gender
    ← from the National Downloadable File (``DAC_NationalDownloadableFile.csv``).
      That file is one row per (NPI × group × practice address); we collapse to
      the clinician's *primary* group — the ``org_pac_id`` they appear under most
      often, largest group breaking ties.

  dac_hospital_affiliations.parquet   many rows per NPI  (the CCN↔NPI bridge)
    npi | ccn | facility_name
    ← ``ccn`` is CMS's ``facility_affiliations_certification_number`` (a hospital
      CCN). Read from whichever CMS ships:
        * the dedicated Facility Affiliation file (``Facility_Affiliation.csv``),
          where ``facility_name`` is the ``facility_type`` label (no proper name
          in that file — a human name comes from a later CCN → Hospital General
          Information join, roadmap step 2); or
        * the National file's wide ``hosp_afl_1..5`` / ``hosp_afl_lbn_1..5``
          columns, unpivoted (``hosp_afl_lbn_N`` *is* the facility name).
      Scoped to the NPIs kept in ``dac_ga.parquet``.

Why Python/DuckDB and not Go: the National file is ~1 GB / ~2.5M rows but the
work is a filter + projection + a window function to pick one group per NPI —
DuckDB's parallel C++ CSV reader pushes the GA / npi_lookup filter down and
finishes in seconds. Not a hand-rolled streaming parse (cf. NPPES). See
../AGENTS.md#the-language-principle.

Caveats (also in reference/doctors-clinicians.md):
  - Medicare-enrolled clinicians only — misses pure-commercial / cash-only and
    most pediatric-only practices.
  - CMS's own directory accuracy is imperfect (stale groups, missing affiliations).
  - ``org_pac_id`` is the *group* PAC ID, not a TIN — it does not line up 1:1
    with Anthem's ``tin_value``; it is a second, independent practice key.

Usage:
  python3 -m reference.doctors_clinicians
      [--dac-url URL | --dac-file PATH]
      [--affiliations-url URL | --affiliations-file PATH]
      [--data-dir data] [--test]
"""
import argparse
import json
import sys
import urllib.request

import duckdb

from ._common import fetch_to_cache_streaming, ref_dir, write_parquet_atomic

PDC_METASTORE = (
    "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items"
    "?show-reference-ids=false"
)
DAC_TITLE = "National Downloadable File"
AFFIL_TITLE = "Facility Affiliation Data"

# Fallbacks if the metastore lookup fails. CMS re-stamps the resource hash on
# every monthly publish — pass --dac-url / --affiliations-url to override.
DAC_URL_FALLBACK = (
    "https://data.cms.gov/provider-data/sites/default/files/resources/"
    "69a75aa9d3dc1aed6b881725cf0ddc12_1755561571/DAC_NationalDownloadableFile.csv"
)
AFFIL_URL_FALLBACK = (
    "https://data.cms.gov/provider-data/sites/default/files/resources/"
    "b1e470bcf3c94e534e0f7255f4b0b8e9_1755561571/Facility_Affiliation.csv"
)


def _resolve_pdc_url(title: str, filename: str) -> str | None:
    """CSV distribution for a Provider Data Catalog dataset, matched by dataset
    title and preferring the distribution whose URL ends with `filename`."""
    try:
        with urllib.request.urlopen(PDC_METASTORE, timeout=30) as r:
            items = json.load(r)
    except Exception as e:  # noqa: BLE001
        print(f"  (metastore lookup failed: {e})", file=sys.stderr)
        return None
    for ds in items:
        if (ds.get("title") or "").strip() != title:
            continue
        csvs = []
        for dist in ds.get("distribution", []):
            data = dist.get("data", dist)
            url = data.get("downloadURL") or ""
            media = data.get("mediaType") or ""
            if url and (media == "text/csv" or url.lower().endswith(".csv")):
                csvs.append(url)
        for url in csvs:
            if url.lower().endswith(filename.lower()):
                return url
        if csvs:
            return csvs[0]
        break  # title matched but no CSV distribution
    return None


def _resolve(url_arg, file_arg, title, filename, fallback):
    if file_arg:
        return None  # fetch_to_cache_streaming short-circuits on `local`
    if url_arg:
        return url_arg
    return _resolve_pdc_url(title, filename) or fallback


def _cols(con, src_sql: str) -> dict:
    """{lower_name: actual_name} for the CSV header — lets the projection be
    written against a canonical name while tolerating CMS's casing / spacing."""
    rows = con.execute(f"DESCRIBE SELECT * FROM {src_sql} LIMIT 0").fetchall()
    return {r[0].lower(): r[0] for r in rows}


def _pick(cols: dict, *candidates: str) -> str | None:
    for c in candidates:
        hit = cols.get(c.lower())
        if hit:
            return hit
    return None


def _q(name: str | None) -> str:
    """A quoted column reference, or SQL NULL when the column is absent."""
    return f'"{name}"' if name else "NULL"


def _read_csv(path: str) -> str:
    return (
        f"read_csv('{path}', header=true, all_varchar=true, "
        f"quote='\"', escape='\"', sample_size=-1, ignore_errors=true)"
    )


def build_national(con, src: str, out_path: str, geo_filter: str) -> None:
    src_sql = _read_csv(src)
    c = _cols(con, src_sql)

    npi = _pick(c, "npi")
    if not npi:
        raise SystemExit("National file has no NPI column")
    last = _pick(c, "provider last name", "lst_nm", "last_name")
    first = _pick(c, "provider first name", "frst_nm", "first_name")
    cred = _pick(c, "cred", "credential")
    spec = _pick(c, "pri_spec", "primary_specialty")
    pac = _pick(c, "org_pac_id")
    org = _pick(c, "facility name", "org_nm", "org_name", "organization_name")
    grd = _pick(c, "grd_yr", "grad_yr", "graduation_year")
    school = _pick(c, "med_sch", "medical_school")
    gender = _pick(c, "gndr", "gender")
    state = _pick(c, "state", "st")
    num_mem = _pick(c, "num_org_mem", "number_of_group_practice_members")

    geo_sql = geo_filter.format(state=_q(state), npi=_q(npi))

    select_sql = f"""
        WITH src AS (
            SELECT
                TRY_CAST({_q(npi)} AS BIGINT)                    AS npi,
                NULLIF(TRIM({_q(last)}), '')                     AS last_name,
                NULLIF(TRIM({_q(first)}), '')                    AS first_name,
                NULLIF(TRIM({_q(cred)}), '')                     AS credential,
                NULLIF(TRIM({_q(spec)}), '')                     AS primary_specialty,
                NULLIF(TRIM({_q(pac)}), '')                      AS org_pac_id,
                NULLIF(TRIM({_q(org)}), '')                      AS org_name,
                TRY_CAST(NULLIF(TRIM({_q(grd)}), '') AS INTEGER) AS grad_year,
                NULLIF(TRIM({_q(school)}), '')                   AS med_school,
                NULLIF(TRIM({_q(gender)}), '')                   AS gender,
                TRY_CAST(NULLIF(TRIM({_q(num_mem)}), '') AS INTEGER) AS num_org_mem
            FROM {src_sql}
            WHERE TRY_CAST({_q(npi)} AS BIGINT) IS NOT NULL
              AND ({geo_sql})
        ),
        by_group AS (
            SELECT *, COUNT(*) OVER (PARTITION BY npi, org_pac_id) AS grp_rows
            FROM src
        ),
        ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY npi
                ORDER BY grp_rows DESC,
                         num_org_mem DESC NULLS LAST,
                         org_pac_id NULLS LAST
            ) AS rn
            FROM by_group
        )
        SELECT npi, last_name, first_name, credential, primary_specialty,
               org_pac_id, org_name, grad_year, med_school, gender
        FROM ranked
        WHERE rn = 1
    """
    write_parquet_atomic(con, select_sql, out_path)


def build_affiliations(con, src: str, out_path: str, dac_ga_path: str) -> None:
    """`src` is either the dedicated Facility Affiliation file or the National
    file carrying wide `hosp_afl_1..5` columns — whichever is present."""
    src_sql = _read_csv(src)
    c = _cols(con, src_sql)

    npi = _pick(c, "npi")
    if not npi:
        raise SystemExit("affiliation source has no NPI column")
    ccn = _pick(
        c,
        "facility_affiliations_certification_number",
        "facility affiliations certification number",
        "ccn",
    )
    ga_in = f"""TRY_CAST({_q(npi)} AS BIGINT) IN (
                    SELECT npi FROM read_parquet('{dac_ga_path}'))"""

    if ccn:
        # dedicated Facility Affiliation file — one row per (NPI × facility)
        ftype = _pick(c, "facility_type", "facility type")
        select_sql = f"""
            SELECT DISTINCT
                TRY_CAST({_q(npi)} AS BIGINT)  AS npi,
                NULLIF(TRIM({_q(ccn)}), '')    AS ccn,
                NULLIF(TRIM({_q(ftype)}), '')  AS facility_name
            FROM {src_sql}
            WHERE TRY_CAST({_q(npi)} AS BIGINT) IS NOT NULL
              AND NULLIF(TRIM({_q(ccn)}), '') IS NOT NULL
              AND {ga_in}
        """
        write_parquet_atomic(con, select_sql, out_path)
        return

    # wide National-file columns: hosp_afl_1..N (CCN) + hosp_afl_lbn_1..N (name)
    legs = []
    for i in range(1, 6):
        afl = _pick(c, f"hosp_afl_{i}")
        if not afl:
            continue
        lbn = _pick(c, f"hosp_afl_lbn_{i}")
        legs.append(f"""
            SELECT TRY_CAST({_q(npi)} AS BIGINT) AS npi,
                   NULLIF(TRIM({_q(afl)}), '')   AS ccn,
                   NULLIF(TRIM({_q(lbn)}), '')   AS facility_name
            FROM {src_sql}
        """)
    if not legs:
        raise SystemExit(
            "affiliation source has neither a CCN column nor hosp_afl_* columns"
        )
    select_sql = f"""
        SELECT DISTINCT npi, ccn, facility_name
        FROM ({' UNION ALL '.join(legs)})
        WHERE npi IS NOT NULL AND ccn IS NOT NULL
          AND npi IN (SELECT npi FROM read_parquet('{dac_ga_path}'))
    """
    write_parquet_atomic(con, select_sql, out_path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dac-url", default=None, help="override the National Downloadable File URL")
    ap.add_argument("--dac-file", default=None, help="use a local National file instead of downloading")
    ap.add_argument("--affiliations-url", default=None, help="override the Facility Affiliation file URL")
    ap.add_argument("--affiliations-file", default=None, help="use a local affiliation file")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    data_dir = "data-test" if args.test else args.data_dir
    rd = ref_dir(args.data_dir, args.test)
    ga_out = f"{rd}/dac_ga.parquet"
    affil_out = f"{rd}/dac_hospital_affiliations.parquet"
    cache_dir = f"{rd}/.cache"

    npi_lookup = f"{data_dir}/anthem/npi_lookup.parquet"

    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")

    # GA scope: prefer the exact set of NPIs we hold Anthem rates for; fall back
    # to the state column when npi_lookup hasn't been built yet.
    try:
        con.execute(f"SELECT 1 FROM read_parquet('{npi_lookup}') LIMIT 1")
        # the National file is read all-varchar; npi_lookup.npi is BIGINT — cast.
        geo_filter = (
            f"TRY_CAST({{npi}} AS BIGINT) IN "
            f"(SELECT npi FROM read_parquet('{npi_lookup}', union_by_name=true))"
        )
        print(f"  GA scope: NPIs in {npi_lookup}")
    except (duckdb.IOException, duckdb.CatalogException):
        geo_filter = "UPPER({state}) = 'GA'"
        print("  GA scope: State = 'GA' (npi_lookup not built)")

    # ── National Downloadable File → dac_ga.parquet ─────────────────────────
    dac_url = _resolve(args.dac_url, args.dac_file, DAC_TITLE,
                       "DAC_NationalDownloadableFile.csv", DAC_URL_FALLBACK)
    print("→ CMS Doctors & Clinicians — National Downloadable File")
    dac_src = fetch_to_cache_streaming(
        f"{cache_dir}/dac_national.csv", [dac_url] if dac_url else [], args.dac_file
    )
    build_national(con, dac_src, ga_out, geo_filter)

    n, groups, gy = con.execute(f"""
        SELECT count(*), count(DISTINCT org_pac_id),
               count(grad_year)
        FROM read_parquet('{ga_out}')
    """).fetchone()
    print(f"→ wrote {ga_out}")
    print(f"  {n:,} clinicians · {groups:,} distinct groups · {gy:,} with a grad year")
    top = con.execute(f"""
        SELECT COALESCE(primary_specialty, '(none)') s, count(*) n
        FROM read_parquet('{ga_out}') GROUP BY 1 ORDER BY n DESC LIMIT 10
    """).fetchall()
    for s, cnt in top:
        print(f"    {cnt:>7,}  {s}")

    # ── hospital affiliations → dac_hospital_affiliations.parquet ──────────
    # Prefer an explicit Facility Affiliation source; otherwise fall back to the
    # National file itself (its wide hosp_afl_* columns, if it carries them).
    if args.affiliations_file or args.affiliations_url:
        affil_url = _resolve(args.affiliations_url, args.affiliations_file, AFFIL_TITLE,
                             "Facility_Affiliation.csv", AFFIL_URL_FALLBACK)
        print("→ CMS Doctors & Clinicians — Facility Affiliation file")
        affil_src = fetch_to_cache_streaming(
            f"{cache_dir}/dac_affiliations.csv",
            [affil_url] if affil_url else [], args.affiliations_file
        )
    elif args.dac_file:
        # fully offline run — don't reach for a separate download
        print("→ affiliations: offline (--dac-file) — reading the National "
              "file's hosp_afl_* columns")
        affil_src = dac_src
    else:
        affil_url = _resolve_pdc_url(AFFIL_TITLE, "Facility_Affiliation.csv")
        if affil_url:
            print("→ CMS Doctors & Clinicians — Facility Affiliation file")
            affil_src = fetch_to_cache_streaming(
                f"{cache_dir}/dac_affiliations.csv", [affil_url], None
            )
        else:
            print("→ affiliations: none published separately — reading the "
                  "National file's hosp_afl_* columns")
            affil_src = dac_src
    build_affiliations(con, affil_src, affil_out, ga_out)

    rows, npis, ccns = con.execute(f"""
        SELECT count(*), count(DISTINCT npi), count(DISTINCT ccn)
        FROM read_parquet('{affil_out}')
    """).fetchone()
    print(f"→ wrote {affil_out}")
    print(f"  {rows:,} affiliations · {npis:,} clinicians · {ccns:,} distinct CCNs")
    kinds = con.execute(f"""
        SELECT COALESCE(facility_name, '(none)') k, count(*) n
        FROM read_parquet('{affil_out}') GROUP BY 1 ORDER BY n DESC LIMIT 10
    """).fetchall()
    for k, cnt in kinds:
        print(f"    {cnt:>7,}  {k}")


if __name__ == "__main__":
    main()
