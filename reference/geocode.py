#!/usr/bin/env python3
"""
Build data/reference/pcp_geocode.parquet — lat/long for every GA PCP-eligible
NPPES provider address, via the free US Census Bulk Geocoding API.

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

PCP_TAXONOMY_CODES below mirrors serving/service_lines.py's constant of the
same name — kept in sync by hand, same as SERVICE_LINE_BILLING_CODES mirrors
the frontend's SERVICE_LINE_CODES (no shared build step between reference/
and serving/, and no existing reference/ builder imports across that
boundary — this one doesn't start).

Addresses are deduped before geocoding (many PCPs share a practice address —
24,697 GA PCP-eligible NPIs resolve to ~13,500 distinct addresses, verified
2026-09-04) and the result rejoined onto every NPI at that address, so a
shared practice doesn't cost Census multiple lookups for one location.

Usage:
  python3 -m reference.geocode [--data-dir data] [--test]
                                [--batch-size N] [--benchmark NAME] [--limit N]
"""
import argparse
import csv
import io
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

import duckdb

from ._common import nppes_dir, ref_dir, write_parquet_atomic

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

# Mirrors serving/service_lines.py:PCP_TAXONOMY_CODES — see the module
# docstring above for why this isn't a cross-package import.
PCP_TAXONOMY_CODES = [
    "207Q00000X",  # Family Medicine (general)
    "207R00000X",  # Internal Medicine (general)
    "208D00000X",  # General Practice
    "363LF0000X",  # Nurse Practitioner — Family
    "363LP2300X",  # Nurse Practitioner — Primary Care
    "363LA2200X",  # Nurse Practitioner — Adult Health
    "363L00000X",  # Nurse Practitioner — generic (no specialization on file)
]


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
    """Census's batch-match CSV -> {id: (lat, lon)} for every row it could
    match; unmatched rows are simply absent, not zeroed. Row shape:
    id, input_address, match_status, match_type, matched_address, "lon,lat",
    tiger_line_id, side."""
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 6:
            continue
        rid, _input, status, _mtype, _matched, latlon = row[:6]
        if status != "Match" or not latlon:
            continue
        try:
            lon, lat = (float(x) for x in latlon.split(","))
            out[int(rid)] = (lat, lon)
        except ValueError:
            continue
    return out


def _geocode_batch(rows: list, benchmark: str, timeout: int = 240, retries: int = 3,
                   census_response_file: str = None) -> dict:
    """rows: [(id, street, city, state, zip), ...], id our own sequential int
    (Census echoes whatever id we send back on each output row). Returns
    {id: (lat, lon)}. `census_response_file` skips the network call entirely
    (hermetic testing) — its contents are parsed as if Census had returned
    them."""
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
        raise SystemExit(f"Census batch geocoder failed after {retries} attempts: {last_err}")
    return _parse_census_response(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
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

    if not os.path.exists(ga_path):
        raise SystemExit(f"{ga_path} not found -- run `make nppes` first")

    con = duckdb.connect()
    placeholders = ", ".join(f"'{c}'" for c in PCP_TAXONOMY_CODES)

    # Distinct addresses first — a shared practice address shouldn't cost
    # Census (or us) a lookup per NPI. row_number() gives each distinct
    # address a stable id for the batch CSV / rejoin.
    con.execute(f"""
        CREATE TEMP TABLE addr AS
        SELECT ROW_NUMBER() OVER (ORDER BY address_line1, address_line2, city, state, postal_code) AS addr_id,
               address_line1, address_line2, city, state, postal_code
        FROM (
            SELECT DISTINCT address_line1, address_line2, city, state, postal_code
            FROM read_parquet('{ga_path}')
            WHERE taxonomy_code IN ({placeholders})
              AND address_line1 IS NOT NULL AND address_line1 <> ''
              AND postal_code IS NOT NULL AND postal_code <> ''
        )
        {f'LIMIT {args.limit}' if args.limit else ''}
    """)
    n_npi, n_addr = con.execute(f"""
        SELECT count(*), (SELECT count(*) FROM addr)
        FROM read_parquet('{ga_path}')
        WHERE taxonomy_code IN ({placeholders})
          AND address_line1 IS NOT NULL AND address_line1 <> ''
          AND postal_code IS NOT NULL AND postal_code <> ''
    """).fetchone()
    print(f"→ {n_npi:,} GA PCP-eligible NPIs -> {n_addr:,} distinct addresses to geocode")
    if n_addr == 0:
        raise SystemExit("no candidate addresses -- has `make nppes` run?")

    addr_rows = con.execute("""
        SELECT addr_id, address_line1, address_line2, city, state, postal_code
        FROM addr ORDER BY addr_id
    """).fetchall()

    batch_rows = []
    for addr_id, a1, a2, city, state, zip5 in addr_rows:
        street = " ".join(x for x in (a1, a2) if x).strip()
        batch_rows.append((addr_id, street, city, state, zip5))

    if args.census_response_file and len(batch_rows) > args.batch_size:
        raise SystemExit("--census-response-file only supplies one canned batch — "
                          "narrow the candidate list with --limit to fit under --batch-size")

    matches: dict = {}
    n_batches = (len(batch_rows) + args.batch_size - 1) // args.batch_size
    for b in range(n_batches):
        chunk = batch_rows[b * args.batch_size:(b + 1) * args.batch_size]
        print(f"  batch {b + 1}/{n_batches} ({len(chunk):,} addresses)...")
        got = _geocode_batch(chunk, args.benchmark,
                             census_response_file=args.census_response_file)
        print(f"    {len(got):,}/{len(chunk):,} matched")
        matches.update(got)

    if not matches:
        raise SystemExit("Census geocoder matched nothing -- check connectivity / API status")

    con.execute("CREATE TEMP TABLE geocoded (addr_id BIGINT, latitude DOUBLE, longitude DOUBLE)")
    con.executemany(
        "INSERT INTO geocoded VALUES (?, ?, ?)",
        [(aid, lat, lon) for aid, (lat, lon) in matches.items()],
    )

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
        JOIN addr a
          ON a.address_line1 = g.address_line1
          AND a.address_line2 IS NOT DISTINCT FROM g.address_line2
          AND a.city = g.city AND a.state = g.state AND a.postal_code = g.postal_code
        JOIN geocoded ge ON ge.addr_id = a.addr_id
        WHERE g.taxonomy_code IN ({placeholders})
          AND ge.latitude BETWEEN {GA_LAT_RANGE[0]} AND {GA_LAT_RANGE[1]}
          AND ge.longitude BETWEEN {GA_LON_RANGE[0]} AND {GA_LON_RANGE[1]}
    """
    write_parquet_atomic(con, select_sql, out_path)

    n_out = con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0]
    addr_rate = 100 * len(matches) / len(batch_rows)
    npi_rate = 100 * n_out / n_npi
    print(f"→ wrote {out_path}")
    print(f"  {n_out:,}/{n_npi:,} NPIs geocoded ({npi_rate:.1f}%) — "
          f"{len(matches):,}/{len(batch_rows):,} addresses matched ({addr_rate:.1f}%)")


if __name__ == "__main__":
    main()
