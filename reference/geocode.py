#!/usr/bin/env python3
"""
Build data/reference/pcp_geocode.parquet — lat/long for every GA PCP- or
imaging-eligible NPPES provider address, and every billing ID in the parsed rate
files, via the free US Census Bulk Geocoding API. A result row is a practice, so
its distance comes from the practice's own address.

  https://geocoding.geo.census.gov/geocoder/Geocoding_Services.html

Why this exists (issue #87, docs/direction.md build sequence step 1):
"who's closest" needs coordinates on the provider file, and this is the free,
keyless way to get them — no Google Maps key, no per-call billing (see
direction.md's "Geography (and the Google Maps question)" section). The batch
endpoint accepts at most 10,000 addresses per request, so geocoding the whole
GA NPPES set (hundreds of thousands of rows, most of them not relevant to any
service line we've built yet) isn't worth doing until a second service line
needs it too. Scoping to the PCP taxonomy allowlist keeps this small and
matches how the rest of the PCP pilot (#83/#87) has scoped its data work.

PCP_TAXONOMY_CODES and IMAGING_TAXONOMY_CODES below are this builder's own
lists of which clinicians' addresses to send.

Addresses are deduped before geocoding (many PCPs share a practice address —
24,697 GA PCP-eligible NPIs resolve to ~13,500 distinct addresses, verified
2026-09-04) and the result rejoined onto every NPI at that address, so a
shared practice doesn't cost Census multiple lookups for one location.

Every address sent, and what Census answered for it (no match included), is
kept in data/reference/geocode_addresses.parquet. A run sends only addresses
that file doesn't hold, so a run with nothing new makes no calls, and the file
is saved after each batch, so a run that fails part-way resumes. `--fresh`
sends every address again.

Usage:
  python3 -m reference.geocode [--data-dir data] [--test] [--fresh]
                                [--batch-size N] [--benchmark NAME] [--limit N]
"""
import argparse
import csv
import datetime
import glob
import io
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

import duckdb

from ._common import nppes_dir, ref_dir, store_dir, write_parquet_atomic

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BENCHMARK = "Public_AR_Current"
MAX_BATCH = 10_000  # Census's hard per-request cap

# Georgia's real bounding box, generous margin. A sanity check, not a second
# geocoder: verified 2026-09-04 that on the real 24,697-candidate run, 7 of
# 21,259 matches (0.03%) landed outside Georgia entirely — Census matching an
# NPPES address to a same-named town in another state (LaFayette GA vs TN,
# Riverdale GA vs MD), one of them a genuine source-data error (an NPPES row
# labeled "DECATUR, GA" carrying an Alabama ZIP). A distance ranking must
# never show a provider states away as "nearby" because of a bad match.
GA_LAT_RANGE = (30.3, 35.1)
GA_LON_RANGE = (-85.7, -80.7)

PCP_TAXONOMY_CODES = [
    "207Q00000X",  # Family Medicine (general)
    "207R00000X",  # Internal Medicine (general)
    "208D00000X",  # General Practice
    "363LF0000X",  # Nurse Practitioner — Family
    "363LP2300X",  # Nurse Practitioner — Primary Care
    "363LA2200X",  # Nurse Practitioner — Adult Health
    "363L00000X",  # Nurse Practitioner — generic (no specialization on file)
]

IMAGING_TAXONOMY_CODES = [
    "2085R0202X", "2085N0700X", "2085B0100X", "2085P0229X", "2085D0003X",
    "2085N0904X", "2085R0204X", "2085U0001X",  # Radiology
    "261QM1200X", "261QR0200X", "261QR0206X", "261QR0207X", "261QR0208X",  # Imaging centers
]

GEOCODE_TAXONOMY_CODES = PCP_TAXONOMY_CODES + IMAGING_TAXONOMY_CODES


def _multipart_body(fields: dict, file_field: str, file_name: str, file_bytes: bytes):
    """Hand-rolled multipart/form-data body. The stdlib has no batteries-
    included multipart POST helper (that's `requests`, not a dependency
    anywhere in this repo — every other reference/ builder is urllib-only).
    Returns (body_bytes, content_type_header)."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
         f'filename="{file_name}"\r\nContent-Type: text/csv\r\n\r\n').encode()
        + file_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _parse_census_response(text: str) -> dict:
    """Census's batch-match CSV -> {id: (match_type, matched_address, lat, lon)}
    for every row it answered. A row it couldn't match (`No_Match`, `Tie`) maps
    to (None, None, None, None), so the address isn't sent again. Row shape:
    id, input_address, match_status, match_type, matched_address, "lon,lat",
    tiger_line_id, side."""
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        try:
            rid = int(row[0])
        except ValueError:
            continue
        out[rid] = (None, None, None, None)
        if row[2] != "Match" or len(row) < 6 or not row[5]:
            continue
        try:
            lon, lat = (float(x) for x in row[5].split(","))
        except ValueError:
            continue
        out[rid] = (row[3] or None, row[4] or None, lat, lon)
    return out


def _geocode_batch(rows: list, benchmark: str, timeout: int = 240, retries: int = 5,
                   census_response_file: str = None) -> dict:
    """rows: [(id, street, city, state, zip), ...], id our own sequential int
    (Census echoes whatever id we send back on each output row). Returns
    _parse_census_response's map. `census_response_file` skips the network
    call entirely (hermetic testing): its contents are parsed as if Census
    had returned them."""
    if census_response_file:
        with open(census_response_file, encoding="utf-8") as f:
            return _parse_census_response(f.read())

    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    body, content_type = _multipart_body(
        {"benchmark": benchmark}, "addressFile", "batch.csv", buf.getvalue().encode("utf-8")
    )
    req = urllib.request.Request(CENSUS_URL, data=body, method="POST",
                                  headers={"Content-Type": content_type})
    last_err = None
    text = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:  # noqa: BLE001
            last_err = e
            print(f"    attempt {attempt}/{retries} failed: {e}", file=sys.stderr)
            if attempt < retries:
                time.sleep(5 * attempt)
    if text is None:
        raise SystemExit(f"Census batch geocoder failed after {retries} attempts: {last_err} "
                         f"(finished batches are saved; re-run to resume)")
    return _parse_census_response(text)


# What Census answered for one address we sent. The first five columns are the
# address exactly as NPPES has it (address_line2 '' when empty), which is the key.
SEEN_COLUMNS = """address_line1 VARCHAR, address_line2 VARCHAR, city VARCHAR, state VARCHAR,
    postal_code VARCHAR, match_type VARCHAR, matched_address VARCHAR, latitude DOUBLE,
    longitude DOUBLE, benchmark VARCHAR, geocoded_on DATE"""
ADDRESS_KEY = "address_line1, address_line2, city, state, postal_code"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    ap.add_argument("--fresh", action="store_true",
                     help="send every address again, ignoring what Census answered before")
    ap.add_argument("--batch-size", type=int, default=MAX_BATCH)
    ap.add_argument("--benchmark", default=BENCHMARK)
    ap.add_argument("--limit", type=int, default=None,
                     help="cap the distinct-address list (smoke-testing)")
    ap.add_argument("--census-response-file", default=None,
                     help="skip the network call; treat this file's contents as "
                          "Census's response CSV (hermetic testing — only "
                          "meaningful with a candidate list that fits one batch)")
    args = ap.parse_args()

    nd = nppes_dir(args.data_dir, args.test)
    rd = ref_dir(args.data_dir, args.test)
    ga_path = f"{nd}/ga_providers.parquet"
    out_path = f"{rd}/pcp_geocode.parquet"
    seen_path = f"{rd}/geocode_addresses.parquet"

    if not os.path.exists(ga_path):
        raise SystemExit(f"{ga_path} not found -- run `make reference STEP=nppes` first")

    con = duckdb.connect()
    placeholders = ", ".join(f"'{c}'" for c in GEOCODE_TAXONOMY_CODES)

    # Billing IDs an MRF names. A TIN that isn't an NPI has no NPPES address to find.
    providers = f"{store_dir(args.data_dir, args.test, 'anthem')}/providers/*.parquet"
    if glob.glob(providers):
        con.execute(f"""CREATE TEMP TABLE billing_npis AS
            SELECT DISTINCT TRY_CAST(tin_value AS BIGINT) AS npi
            FROM read_parquet('{providers}', union_by_name=true)""")
    else:
        con.execute("CREATE TEMP TABLE billing_npis (npi BIGINT)")
    candidate = f"(taxonomy_code IN ({placeholders}) OR npi IN (SELECT npi FROM billing_npis))"

    con.execute(f"CREATE TEMP TABLE seen ({SEEN_COLUMNS})")
    if os.path.exists(seen_path) and not args.fresh:
        con.execute(f"INSERT INTO seen SELECT {ADDRESS_KEY}, match_type, matched_address, latitude, "
                    f"longitude, benchmark, geocoded_on FROM read_parquet('{seen_path}')")

    # Distinct addresses first — a shared practice address shouldn't cost
    # Census (or us) a lookup per NPI.
    con.execute(f"""
        CREATE TEMP TABLE candidates AS
        SELECT DISTINCT address_line1, COALESCE(address_line2, '') AS address_line2,
               city, state, postal_code
        FROM read_parquet('{ga_path}')
        WHERE {candidate}
          AND address_line1 IS NOT NULL AND address_line1 <> ''
          AND postal_code IS NOT NULL AND postal_code <> ''
    """)
    # Hospitals' own addresses too (`make reference STEP=hospitals`), so a hospital
    # gets coordinates and Census's standardized address to compare a biller's with.
    # They land only in geocode_addresses.parquet, which is keyed by address.
    hospitals = f"{rd}/hospitals_ga.parquet"
    n_hosp = 0
    if os.path.exists(hospitals):
        n_hosp = con.execute(f"SELECT count(*) FROM read_parquet('{hospitals}')").fetchone()[0]
        con.execute(f"""
            INSERT INTO candidates
            SELECT DISTINCT address, '', city, state, postal_code FROM read_parquet('{hospitals}')
            WHERE address IS NOT NULL AND address <> '' AND postal_code IS NOT NULL AND postal_code <> ''
            EXCEPT SELECT * FROM candidates
        """)
    # Only addresses Census hasn't answered for. row_number() gives each one an
    # id for the batch CSV, which Census echoes back.
    con.execute(f"""
        CREATE TEMP TABLE unseen AS
        SELECT ROW_NUMBER() OVER (ORDER BY {ADDRESS_KEY}) AS addr_id, *
        FROM (SELECT * FROM candidates EXCEPT SELECT {ADDRESS_KEY} FROM seen)
        ORDER BY addr_id
        {f'LIMIT {args.limit}' if args.limit else ''}
    """)
    n_npi, n_addr, n_new = con.execute(f"""
        SELECT count(*), (SELECT count(*) FROM candidates), (SELECT count(*) FROM unseen)
        FROM read_parquet('{ga_path}')
        WHERE {candidate}
          AND address_line1 IS NOT NULL AND address_line1 <> ''
          AND postal_code IS NOT NULL AND postal_code <> ''
    """).fetchone()
    print(f"→ {n_npi:,} GA NPIs (PCP, imaging or billing ID) and {n_hosp:,} hospitals -> "
          f"{n_addr:,} distinct addresses, {n_new:,} not sent to Census before")
    if n_addr == 0:
        raise SystemExit("no candidate addresses -- has `make reference STEP=nppes` run?")

    batch_rows = [(addr_id, " ".join(x for x in (a1, a2) if x).strip(), city, state, zip5)
                  for addr_id, a1, a2, city, state, zip5 in con.execute(
                      "SELECT addr_id, address_line1, address_line2, city, state, postal_code FROM unseen").fetchall()]

    if args.census_response_file and len(batch_rows) > args.batch_size:
        raise SystemExit("--census-response-file only supplies one canned batch — "
                          "narrow the candidate list with --limit to fit under --batch-size")

    today = datetime.date.today()
    n_batches = (len(batch_rows) + args.batch_size - 1) // args.batch_size
    for b in range(n_batches):
        chunk = batch_rows[b * args.batch_size:(b + 1) * args.batch_size]
        print(f"  batch {b + 1}/{n_batches} ({len(chunk):,} addresses)...")
        got = _geocode_batch(chunk, args.benchmark, census_response_file=args.census_response_file)
        print(f"    {sum(1 for r in got.values() if r[2] is not None):,}/{len(chunk):,} matched")
        if not got:
            raise SystemExit("Census answered no address in the batch -- check connectivity / API status")
        con.execute("CREATE OR REPLACE TEMP TABLE answered (addr_id BIGINT, match_type VARCHAR, "
                    "matched_address VARCHAR, latitude DOUBLE, longitude DOUBLE)")
        con.executemany("INSERT INTO answered VALUES (?, ?, ?, ?, ?)",
                        [(aid, *result) for aid, result in got.items()])
        con.execute(f"""
            INSERT INTO seen
            SELECT {ADDRESS_KEY}, a.match_type, a.matched_address, a.latitude, a.longitude, ?, ?
            FROM unseen u JOIN answered a USING (addr_id)
        """, [args.benchmark, today])
        # Saved after each batch, so a failure part-way loses at most one batch.
        write_parquet_atomic(con, f"SELECT * FROM seen ORDER BY {ADDRESS_KEY}", seen_path)

    if n_batches == 0:
        if args.fresh or not os.path.exists(seen_path):
            write_parquet_atomic(con, f"SELECT * FROM seen ORDER BY {ADDRESS_KEY}", seen_path)
        print("  nothing new to send to Census")

    con.execute(f"""
        CREATE TEMP TABLE geocoded AS
        SELECT c.*, s.latitude, s.longitude
        FROM candidates c JOIN seen s USING ({ADDRESS_KEY})
        WHERE s.latitude IS NOT NULL
    """)
    if con.execute("SELECT count(*) FROM geocoded").fetchone()[0] == 0:
        raise SystemExit("Census geocoder matched nothing -- check connectivity / API status")


    out_of_ga = con.execute(f"""
        SELECT count(*) FROM geocoded
        WHERE latitude NOT BETWEEN {GA_LAT_RANGE[0]} AND {GA_LAT_RANGE[1]}
           OR longitude NOT BETWEEN {GA_LON_RANGE[0]} AND {GA_LON_RANGE[1]}
    """).fetchone()[0]
    if out_of_ga:
        print(f"    dropping {out_of_ga:,} address(es) geocoded outside Georgia's "
              f"bounding box (a wrong-state match, not a real position)")

    select_sql = f"""
        SELECT g.npi, ge.latitude, ge.longitude
        FROM read_parquet('{ga_path}') g
        JOIN geocoded ge
          ON ge.address_line1 = g.address_line1
          AND ge.address_line2 = COALESCE(g.address_line2, '')
          AND ge.city = g.city AND ge.state = g.state AND ge.postal_code = g.postal_code
        WHERE {candidate}
          AND ge.latitude BETWEEN {GA_LAT_RANGE[0]} AND {GA_LAT_RANGE[1]}
          AND ge.longitude BETWEEN {GA_LON_RANGE[0]} AND {GA_LON_RANGE[1]}
    """
    write_parquet_atomic(con, select_sql, out_path)

    n_out = con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0]
    n_matched = con.execute("SELECT count(*) FROM geocoded").fetchone()[0]
    print(f"→ wrote {out_path}")
    print(f"  {n_out:,}/{n_npi:,} NPIs geocoded ({100 * n_out / n_npi:.1f}%), "
          f"{n_matched:,}/{n_addr:,} addresses matched ({100 * n_matched / n_addr:.1f}%)")


if __name__ == "__main__":
    main()
