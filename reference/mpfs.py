#!/usr/bin/env python3
"""
Build data/reference/mpfs_ga.parquet — the Medicare Physician Fee Schedule
allowed amount for every payable (HCPCS/CPT × modifier × facility/non-facility)
in the two Georgia localities.

  "CMS Physician Fee Schedule — Relative Value Files"
  https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files

Why this exists (issue #61): Anthem's MRF prices ~70 network-administration
"provider groups", not providers, so most provider drill-downs have no
per-provider rate. A Medicare allowed amount for every code gives (a) a sanity
check on group rates — "$180 vs Medicare's $60 = plausible" vs the $4.4M
drug-code garbage (#51) — and (b) a fallback price when the MRF is group-rate
noise. `serving/benchmark.medicare_allowed()` reads this file; `/rates/quote`
returns it as `medicare_allowed` + a `vs_medicare` ratio.

The allowed amount per (code × modifier × POS × locality):

    allowed = (workRVU·GPCI_w + peRVU·GPCI_pe + mpRVU·GPCI_mp) × conversionFactor

  - workRVU / peRVU (facility + non-facility) / mpRVU + a status indicator come
    from the quarterly RVU file (PPRRVUxx_*.csv).
  - the three geographic practice-cost indices come from the GPCI file
    (GPCIxxxx.csv) — same zip as the RVU file.
  - the conversion factor is one number, read from the RVU file's own
    "CONVERSION FACTOR" column when present, else --cf / CF_BY_YEAR.

Status indicator (PPRRVU "STATUS CODE"):
  A active · R restricted (still paid) · T paid only if the sole service
    → allowed amount computed.
  C carrier-priced (no national RVUs) → row kept, `medicare_allowed` NULL (a
    "Medicare covers this, amount set locally" flag).
  B/N/I/P/X/E and everything else (bundled / not covered / not valid /
    statutory exclusion) → dropped.

Caveat: this is the *physician* fee schedule. Facility fees are OPPS (hospital
outpatient) / ASC / IPPS — separate schedules, not modelled here. See
reference/mpfs.md.

Usage:
  python3 -m reference.mpfs [--cms-url ZIP_URL] [--year N] [--cf FLOAT]
                            [--rvu-file PPRRVU.csv] [--gpci-file GPCI.csv]
                            [--data-dir data] [--test]
"""
import argparse
import os
import re
import sys
import zipfile

import duckdb

from ._common import fetch_to_cache, ref_dir, write_parquet_atomic

# CMS re-stamps the RVU zip each quarter: rvu<YY><q>.zip (q = a/b/c/d). Pass
# --cms-url to override. This fallback is the 2025 fourth-quarter release.
CMS_ZIP_FALLBACK = "https://www.cms.gov/files/zip/rvu25d.zip"

# Conversion factor by calendar year — used only when the RVU file carries no
# "CONVERSION FACTOR" column and --cf is not given. VERIFY against the CMS
# final rule before a production run (2025 had a mid-year change). $/RVU.
CF_BY_YEAR = {
    2023: 33.8872,
    2024: 32.7442,
    2025: 32.3465,
}

# Payable statuses — an allowed amount is computed only for these.
PAYABLE = ("A", "R", "T")
# Statuses with no computable fee-schedule amount and no reason to keep.
DROP_STATUS = ("B", "N", "I", "P", "X", "E")


def _year_from_name(name: str) -> int:
    """rvu25d.zip / PPRRVU25_OCT.csv -> 2025. 0 if not found."""
    m = re.search(r"(?:rvu|PPRRVU)(\d{2})", name, re.IGNORECASE)
    return 2000 + int(m.group(1)) if m else 0


def _find_header(path: str, *needles: str) -> int:
    """The CSV releases carry title/preamble lines before the real header. Return
    the 0-based index of the line whose first cell starts with any of `needles`
    (so `read_csv(..., skip=i)` lands on it). 0 if there's no preamble."""
    lowered = [n.lower() for n in needles]
    try:
        with open(path, "r", encoding="latin-1", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 60:
                    break
                cell0 = line.split(",", 1)[0].strip().strip('"').lower()
                if any(cell0 == n or cell0.startswith(n) for n in lowered):
                    return i
    except OSError:
        pass
    return 0


def _line(path: str, idx: int) -> str:
    """The idx-th line of a text file (for peeking at a header row)."""
    with open(path, "r", encoding="latin-1", errors="replace") as f:
        for i, line in enumerate(f):
            if i == idx:
                return line
    return ""


# PPRRVU CSV record layout — stable for years, and the CSV's own leaf-header row
# is unusable ("CODE", "RVU", "PE RVU", "INDICATOR", …), so the core RVU columns
# are read positionally. 0-based column index → meaning:
RVU_COL = {
    "hcpcs": 0, "mod": 1, "status": 3, "work": 5,
    "nonfac_pe": 6, "nonfac_na": 7, "fac_pe": 8, "fac_na": 9, "mp": 10,
}
RVU_CF_COL = 24  # "CONV FACTOR" — validated against a plausible $/RVU range


def _extract_from_zip(zip_path: str, dest_dir: str) -> tuple[str, str]:
    """Pull the PPRRVU*.csv and GPCI*.csv members out of the RVU zip."""
    os.makedirs(dest_dir, exist_ok=True)
    rvu_member = gpci_member = None
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            base = os.path.basename(n).upper()
            if not base.endswith(".CSV"):
                continue
            if base.startswith("PPRRVU"):
                rvu_member = rvu_member or n
            elif base.startswith("GPCI"):
                gpci_member = gpci_member or n
        if not rvu_member or not gpci_member:
            raise SystemExit(
                f"{zip_path}: expected a PPRRVU*.csv and a GPCI*.csv inside "
                f"(found rvu={rvu_member!r} gpci={gpci_member!r}). "
                f"Pass --rvu-file / --gpci-file to point at extracted copies."
            )
        out = {}
        for member in (rvu_member, gpci_member):
            target = os.path.join(dest_dir, os.path.basename(member))
            with z.open(member) as src, open(target, "wb") as dst:
                dst.write(src.read())
            out[member] = target
    return out[rvu_member], out[gpci_member]


def _cols(con, rel: str) -> dict:
    """{normalized_name: actual_name} for a relation — lets the SELECT tolerate
    the RVU/GPCI files' shifting column spelling across years."""
    rows = con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()
    return {re.sub(r"[^a-z0-9]", "", c[0].lower()): c[0] for c in rows}


def _pick(colmap: dict, *needles, exclude=()) -> str:
    """First actual column whose normalized name contains every needle and no
    excluded token. Raises if nothing matches."""
    for norm, actual in colmap.items():
        if all(n in norm for n in needles) and not any(x in norm for x in exclude):
            return actual
    raise SystemExit(f"no column matching {needles} (exclude {exclude}) in {list(colmap.values())}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cms-url", default=None, help="override the RVU zip URL")
    ap.add_argument("--rvu-file", default=None, help="local PPRRVU*.csv (skips the download)")
    ap.add_argument("--gpci-file", default=None, help="local GPCI*.csv (skips the download)")
    ap.add_argument("--year", type=int, default=None,
                    help="calendar year to stamp (default: inferred from the URL)")
    ap.add_argument("--cf", type=float, default=None,
                    help="conversion factor override ($/RVU)")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    rd = ref_dir(args.data_dir, args.test)
    cache_dir = f"{rd}/.cache"
    out_path = f"{rd}/mpfs_ga.parquet"

    url = args.cms_url or CMS_ZIP_FALLBACK
    year = (args.year or _year_from_name(args.rvu_file or "")
            or _year_from_name(url) or 0)

    print(f"→ CMS Physician Fee Schedule — Relative Value Files"
          f"{f' (CY {year})' if year else ''}")

    if args.rvu_file and args.gpci_file:
        rvu_csv, gpci_csv = args.rvu_file, args.gpci_file
    else:
        zip_path = fetch_to_cache(
            f"{cache_dir}/rvu_{year or 'x'}.zip", [url],
            header_check=b"PK",  # a zip's magic bytes
        )
        rvu_csv, gpci_csv = _extract_from_zip(zip_path, cache_dir)
    print(f"  RVU  : {rvu_csv}")
    print(f"  GPCI : {gpci_csv}")

    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")

    # ── RVU file: named columns (our test fixture) or positional (the real
    # PPRRVU CSV, whose leaf-header row is "CODE, RVU, PE RVU, INDICATOR, …") ──
    rvu_hdr = _find_header(rvu_csv, "HCPCS")
    hdr_line = _line(rvu_csv, rvu_hdr).lower()
    named = "status code" in hdr_line and "work rvu" in hdr_line

    if named:
        rvu_rel = (f"read_csv('{rvu_csv}', header=true, all_varchar=true, "
                   f"skip={rvu_hdr}, sample_size=-1, ignore_errors=true)")
        rc = _cols(con, rvu_rel)
        c_hcpcs = f'"{_pick(rc, "hcpcs")}"'
        c_mod = f'"{_pick(rc, "mod")}"' if any("mod" in k for k in rc) else "''"
        c_status = f'"{_pick(rc, "status")}"'
        c_work = f'"{_pick(rc, "work", "rvu")}"'
        c_nfpe = f'"{_pick(rc, "nonfac", "pe", "rvu")}"'
        c_fpe = f'"{_pick(rc, "facility", "pe", "rvu", exclude=("nonfac",))}"'
        c_mp = f'"{_pick(rc, "mp", "rvu")}"'
        c_nfna = next((f'"{rc[k]}"' for k in rc if "nonfac" in k and "na" in k and "indicator" in k), "''")
        c_fna = next((f'"{rc[k]}"' for k in rc
                      if "fac" in k and "na" in k and "indicator" in k and "nonfac" not in k), "''")
        c_cf = next((f'"{rc[k]}"' for k in rc if "conversion" in k and "factor" in k), None)
    else:
        # header=false → columns are column0, column1, …; take them by index.
        rvu_rel = (f"read_csv('{rvu_csv}', header=false, all_varchar=true, "
                   f"skip={rvu_hdr + 1}, sample_size=-1, ignore_errors=true, "
                   f"null_padding=true)")
        cl = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {rvu_rel}").fetchall()]

        def at(i):
            return f'"{cl[i]}"' if i < len(cl) else "NULL"

        c_hcpcs, c_mod, c_status = at(RVU_COL["hcpcs"]), at(RVU_COL["mod"]), at(RVU_COL["status"])
        c_work, c_nfpe, c_fpe = at(RVU_COL["work"]), at(RVU_COL["nonfac_pe"]), at(RVU_COL["fac_pe"])
        c_nfna, c_fna, c_mp = at(RVU_COL["nonfac_na"]), at(RVU_COL["fac_na"]), at(RVU_COL["mp"])
        c_cf = at(RVU_CF_COL) if RVU_CF_COL < len(cl) else None
    print(f"  RVU columns: {'named header' if named else 'positional (PPRRVU layout)'}")

    # ── GPCI file (clean single header, names are stable-ish) ──────────────
    gpci_hdr = _find_header(gpci_csv, "Medicare Administrative Contractor", "MAC",
                            "State", "Locality")
    gpci_rel = (f"read_csv('{gpci_csv}', header=true, all_varchar=true, "
                f"skip={gpci_hdr}, sample_size=-1, ignore_errors=true)")
    gc = _cols(con, gpci_rel)
    g_loc_num = _pick(gc, "locality", exclude=("name",))
    g_loc_name = next((gc[k] for k in gc if "locality" in k and "name" in k), None)
    g_pw = _pick(gc, "gpci", "pw") if any("pw" in k and "gpci" in k for k in gc) \
        else _pick(gc, "gpci", "work")
    g_pe = _pick(gc, "gpci", "pe", exclude=("pw",))
    g_mp = _pick(gc, "gpci", "mp")
    g_state = next((gc[k] for k in gc if "state" in k), None)

    mod_sql = f"COALESCE(NULLIF(TRIM({c_mod}), ''), '')"
    nonfac_na_sql = f"UPPER(COALESCE(TRIM({c_nfna}), ''))"
    fac_na_sql = f"UPPER(COALESCE(TRIM({c_fna}), ''))"

    # ── Georgia GPCI localities ────────────────────────────────────────────
    if g_state:
        ga_where = f"UPPER(TRIM(\"{g_state}\")) IN ('GEORGIA', 'GA', '13', '11')"
    elif g_loc_name:
        ga_where = (f"UPPER(\"{g_loc_name}\") LIKE '%GEORGIA%' "
                    f"OR UPPER(\"{g_loc_name}\") LIKE '%ATLANTA%'")
    else:
        raise SystemExit("GPCI file has neither a State nor a Locality Name column "
                         "to isolate Georgia — inspect it and pass a patched copy.")

    con.execute(f"""
        CREATE TABLE gpci AS
        SELECT
            LPAD(REGEXP_REPLACE(COALESCE(TRIM("{g_loc_num}"), ''), '[^0-9]', '', 'g'), 2, '0') AS locality,
            {f'"{g_loc_name}"' if g_loc_name else "NULL"} AS locality_name,
            TRY_CAST("{g_pw}" AS DOUBLE) AS pw_gpci,
            TRY_CAST("{g_pe}" AS DOUBLE) AS pe_gpci,
            TRY_CAST("{g_mp}" AS DOUBLE) AS mp_gpci
        FROM {gpci_rel}
        WHERE ({ga_where})
          AND TRY_CAST("{g_pw}" AS DOUBLE) IS NOT NULL
    """)
    localities = con.execute(
        "SELECT locality, locality_name, pw_gpci, pe_gpci, mp_gpci FROM gpci ORDER BY locality"
    ).fetchall()
    if not localities:
        raise SystemExit("no Georgia rows in the GPCI file — check the GA filter")
    print(f"  Georgia localities: "
          + ", ".join(f"{l[0]}({(l[1] or '').strip()})" for l in localities))

    # ── conversion factor ─────────────────────────────────────────────────
    cf = args.cf
    cf_src = "--cf"
    if cf is None and c_cf:
        cand = con.execute(
            f"SELECT MODE(TRY_CAST({c_cf} AS DOUBLE)) FROM {rvu_rel} "
            f"WHERE TRY_CAST({c_cf} AS DOUBLE) BETWEEN 15 AND 120"
        ).fetchone()[0]
        if cand:
            cf, cf_src = cand, f"RVU file col {c_cf}"
    if cf is None:
        cf = CF_BY_YEAR.get(year)
        cf_src = f"CF_BY_YEAR[{year}]"
    if not cf:
        raise SystemExit(f"no conversion factor (year {year}) — pass --cf")
    print(f"  conversion factor: {cf}  (from {cf_src})")

    con.execute(f"""
        CREATE TABLE rvu AS
        SELECT
            TRIM({c_hcpcs})                   AS billing_code,
            {mod_sql}                         AS modifier,
            UPPER(TRIM({c_status}))           AS status,
            TRY_CAST({c_work} AS DOUBLE)      AS work_rvu,
            TRY_CAST({c_nfpe} AS DOUBLE)      AS nonfac_pe_rvu,
            TRY_CAST({c_fpe} AS DOUBLE)       AS fac_pe_rvu,
            TRY_CAST({c_mp} AS DOUBLE)        AS mp_rvu,
            {nonfac_na_sql}                   AS nonfac_na,
            {fac_na_sql}                      AS fac_na
        FROM {rvu_rel}
        WHERE {c_hcpcs} IS NOT NULL AND TRIM({c_hcpcs}) <> ''
          AND REGEXP_FULL_MATCH(TRIM({c_hcpcs}), '[0-9A-Za-z]{{5}}')
    """)

    src_rows = con.execute("SELECT count(*) FROM rvu").fetchone()[0]
    by_status = con.execute(
        "SELECT status, count(*) FROM rvu GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    print(f"  {src_rows:,} RVU rows  ·  status: "
          + " ".join(f"{s or '?'}={n}" for s, n in by_status))

    payable_in = ", ".join(f"'{s}'" for s in PAYABLE)
    drop_in = ", ".join(f"'{s}'" for s in DROP_STATUS)

    select_sql = f"""
        WITH base AS (
            SELECT
                billing_code,
                CASE WHEN REGEXP_FULL_MATCH(billing_code, '[0-9]{{5}}')
                     THEN 'CPT' ELSE 'HCPCS' END AS billing_code_type,
                modifier, status,
                COALESCE(work_rvu, 0)      AS work_rvu,
                COALESCE(nonfac_pe_rvu, 0) AS nonfac_pe_rvu,
                COALESCE(fac_pe_rvu, 0)    AS fac_pe_rvu,
                COALESCE(mp_rvu, 0)        AS mp_rvu,
                nonfac_na, fac_na
            FROM rvu
            WHERE status NOT IN ({drop_in})
        ),
        priced AS (
            SELECT b.*, g.locality, g.pw_gpci, g.pe_gpci, g.mp_gpci, {cf} AS cf
            FROM base b CROSS JOIN gpci g
        ),
        expanded AS (
            SELECT billing_code, billing_code_type, modifier, 'nonfacility' AS pos,
                   locality, status,
                   CASE WHEN status IN ({payable_in}) AND nonfac_na <> 'NA' THEN
                       NULLIF(ROUND((work_rvu * pw_gpci + nonfac_pe_rvu * pe_gpci
                                     + mp_rvu * mp_gpci) * cf, 2), 0)
                   END AS medicare_allowed
            FROM priced
            UNION ALL
            SELECT billing_code, billing_code_type, modifier, 'facility' AS pos,
                   locality, status,
                   CASE WHEN status IN ({payable_in}) AND fac_na <> 'NA' THEN
                       NULLIF(ROUND((work_rvu * pw_gpci + fac_pe_rvu * pe_gpci
                                     + mp_rvu * mp_gpci) * cf, 2), 0)
                   END AS medicare_allowed
            FROM priced
        )
        SELECT billing_code, billing_code_type, modifier, pos, locality,
               medicare_allowed, status
        FROM expanded
        WHERE medicare_allowed IS NOT NULL OR status = 'C'
        ORDER BY billing_code, modifier, pos, locality
    """
    write_parquet_atomic(con, select_sql, out_path)

    kept, codes, priced_codes, carrier = con.execute(f"""
        SELECT count(*), count(DISTINCT billing_code),
               count(DISTINCT billing_code) FILTER (WHERE medicare_allowed IS NOT NULL),
               count(DISTINCT billing_code) FILTER (WHERE status = 'C')
        FROM read_parquet('{out_path}')
    """).fetchone()
    print(f"→ wrote {out_path}")
    print(f"  {kept:,} rows · {codes:,} codes ({priced_codes:,} with an allowed "
          f"amount, {carrier:,} carrier-priced) × {len(localities)} localities × 2 POS")

    sample = con.execute(f"""
        SELECT billing_code, modifier, pos, locality, medicare_allowed
        FROM read_parquet('{out_path}')
        WHERE medicare_allowed IS NOT NULL
        ORDER BY billing_code, modifier, pos, locality LIMIT 8
    """).fetchall()
    for bc, m, p, loc, amt in sample:
        print(f"    {bc:<8} {m or '--':<3} {p:<11} loc {loc}  ${amt:,.2f}")


if __name__ == "__main__":
    main()
