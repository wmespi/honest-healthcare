#!/usr/bin/env python3
"""
Build data/ingestion/reference/geocode_addresses.parquet - where each address the
find-care build puts something at is, and which geocoder said so
(integration/build.md).

- Census. Every address goes to the free US Census batch geocoder: the NPPES
  addresses, main and secondary, of every billing ID in the parsed rate files,
  each Georgia hospital's address, and each address Doctors & Clinicians lists
  a clinician at. Its answer carries Census's standardized address, which is
  how the build knows two spellings are one building.
- OpenStreetMap. A biller's or hospital's address Census can't put in Georgia
  then goes to OpenStreetMap's Nominatim, which knows buildings Census's street
  ranges don't. Only an answer that is the address itself is kept as a point:
  the same house number, in Georgia, in the same ZIP code or city. A street or
  a town would put a place on the map where it isn't.

  https://geocoding.geo.census.gov/geocoder/Geocoding_Services.html
  https://operations.osmfoundation.org/policies/nominatim/

Why these two: both are free and keyless, with no per-call billing (see
direction.md's "Geography (and the Google Maps question)" section). Census's
batch endpoint takes up to 10,000 addresses a request, and addresses are deduped
before they're sent, so a building many billers share costs one lookup.
Nominatim allows one request a second, so it gets only what Census misses.

Every address sent, and what each geocoder answered for it (no match included),
is kept, one row per address and geocoder. `located` says the answer is a point
inside Georgia's box. A run sends a geocoder only the addresses the file holds no
answer from it for, so a run with nothing new makes no calls, and the file is
saved as answers arrive, so a run that fails part-way resumes. If OpenStreetMap
stops answering, the run says how many addresses are left and ends without them,
and the next run asks. `--fresh` sends every address again.

Usage:
  python3 -m ingestion.reference.geocode [--data-dir data] [--test] [--fresh]
                                [--batch-size N] [--benchmark NAME] [--limit N]
"""
import argparse
import csv
import datetime
import glob
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import duckdb

from ._common import raw_dir, store_dir, write_parquet_atomic

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BENCHMARK = "Public_AR_Current"
MAX_BATCH = 10_000  # Census's hard per-request cap

OSM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy: one request a second at most, from one thread, under
# a User-Agent that says who is asking, and no address asked twice.
OSM_USER_AGENT = "honest-healthcare (https://github.com/wmespi/honest-healthcare)"
OSM_PAUSE = 1.1
OSM_SAVE_EVERY = 25
# A suite, floor or unit and what follows it, which OpenStreetMap's streets don't carry.
UNIT = re.compile(r"[\s,]+(?:(?:STE|SUITE|UNIT|BLDG|BUILDING|FL|FLOOR|RM|ROOM|APT)\b|#).*$", re.I)

# Georgia's bounding box. Every address sent is a Georgia one, so an answer
# outside it is a mistake, and `located` says an answer is a point inside it.
GEORGIA_LAT, GEORGIA_LON = (30.3, 35.1), (-85.7, -80.7)
LOCATED = (f"coalesce(latitude BETWEEN {GEORGIA_LAT[0]} AND {GEORGIA_LAT[1]} "
           f"AND longitude BETWEEN {GEORGIA_LON[0]} AND {GEORGIA_LON[1]}, false)")

def _multipart_body(fields: dict, file_field: str, file_name: str, file_bytes: bytes):
    """Hand-rolled multipart/form-data body. The stdlib has no batteries-
    included multipart POST helper (that's `requests`, not a dependency
    anywhere in this repo - every other ingestion/reference/ builder is urllib-only).
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


class OsmUnavailable(Exception):
    """Nominatim didn't answer a request, after retries."""


def _osm_query(line1: str, city: str, postal_code: str):
    """Nominatim's structured query for one address, or None when the street has
    no house number to check an answer against."""
    street = UNIT.sub("", line1).strip()
    if not street[:1].isdigit():
        return None
    return {"street": street, "city": city.strip(), "state": "GA", "postalcode": postal_code[:5]}


def _osm_point(results: list, query: dict) -> tuple:
    """(kind, lat, lon) from the first of Nominatim's answers that is the address
    itself: the same house number, in Georgia, in the same ZIP code or city.
    Anything else (no answer, a street, a town) is (None, None, None)."""
    number = query["street"].split(" ")[0].upper()
    for r in results:
        a = r.get("address") or {}
        towns = {str(a.get(k, "")).upper() for k in ("city", "town", "village", "hamlet", "municipality")}
        if (str(a.get("house_number", "")).upper() == number and a.get("state") == "Georgia"
                and (str(a.get("postcode", ""))[:5] == query["postalcode"] or query["city"].upper() in towns)):
            try:
                return r.get("addresstype") or r.get("type"), float(r["lat"]), float(r["lon"])
            except (KeyError, TypeError, ValueError):
                continue
    return None, None, None


def _ask_osm(query: dict, retries: int = 3, timeout: int = 30) -> list:
    """Nominatim's answers for one structured query, pausing after every request
    so no two are less than a second apart."""
    params = urllib.parse.urlencode({**query, "countrycodes": "us", "format": "jsonv2",
                                     "addressdetails": 1, "limit": 3})
    req = urllib.request.Request(f"{OSM_URL}?{params}", headers={"User-Agent": OSM_USER_AGENT})
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(10 * attempt)
        finally:
            time.sleep(OSM_PAUSE)
    raise OsmUnavailable(str(last_err))


# What one geocoder answered for one address we sent. The first five columns are
# the address exactly as its source has it (address_line2 '' when empty), and with
# `source` (census or openstreetmap) they are the key. matched_address and
# benchmark are Census's alone.
SEEN_COLUMNS = """address_line1 VARCHAR, address_line2 VARCHAR, city VARCHAR, state VARCHAR,
    postal_code VARCHAR, source VARCHAR, match_type VARCHAR, matched_address VARCHAR,
    latitude DOUBLE, longitude DOUBLE, located BOOLEAN, benchmark VARCHAR, geocoded_on DATE"""
ADDRESS_KEY = "address_line1, address_line2, city, state, postal_code"


def _save(con, seen_path: str) -> None:
    write_parquet_atomic(con, f"SELECT * FROM seen ORDER BY {ADDRESS_KEY}, source", seen_path)


def _ask_openstreetmap(con, seen_path: str, today, limit: int = None, response_file: str = None) -> None:
    """Ask OpenStreetMap for each biller's or hospital's address Census couldn't
    put in Georgia and it hasn't answered for. `response_file` stands in for the
    network (hermetic testing): JSON of {"street|city|zip": [answers]}, where an
    address it doesn't list has no answer."""
    rows = con.execute(f"""
        SELECT * FROM (
            SELECT {ADDRESS_KEY} FROM candidates JOIN seen USING ({ADDRESS_KEY})
            WHERE place AND source = 'census' AND NOT located
            EXCEPT SELECT {ADDRESS_KEY} FROM seen WHERE source = 'openstreetmap')
        ORDER BY ALL
        {f'LIMIT {limit}' if limit else ''}
    """).fetchall()
    asks = [(row, query) for row in rows if (query := _osm_query(row[0], row[2], row[4]))]
    print(f"  {len(rows):,} of billers' and hospitals' addresses Census couldn't place: "
          f"{len(asks):,} to ask OpenStreetMap, {len(rows) - len(asks):,} with no house number to ask about")
    if not asks:
        return
    canned = None
    if response_file:
        with open(response_file, encoding="utf-8") as f:
            canned = json.load(f)
    found = done = 0
    try:
        for row, query in asks:
            if canned is None:
                results = _ask_osm(query)
            else:
                results = canned.get("|".join(query[k] for k in ("street", "city", "postalcode")), [])
            kind, lat, lon = _osm_point(results, query)
            con.execute(f"""
                INSERT INTO seen
                SELECT {ADDRESS_KEY}, 'openstreetmap', match_type, NULL, latitude, longitude, {LOCATED}, NULL, geocoded_on
                FROM (VALUES (?, ?, ?, ?, ?, ?::VARCHAR, ?::DOUBLE, ?::DOUBLE, ?::DATE))
                     AS t({ADDRESS_KEY}, match_type, latitude, longitude, geocoded_on)
            """, [*row, kind, lat, lon, today])
            done += 1
            found += lat is not None
            if done % OSM_SAVE_EVERY == 0:
                _save(con, seen_path)
                print(f"    {done:,}/{len(asks):,} asked, {found:,} located")
    except OsmUnavailable as e:
        print(f"  ⚠ OpenStreetMap stopped answering ({e}): {len(asks) - done:,} addresses are left "
              f"for the next run", file=sys.stderr)
    finally:
        _save(con, seen_path)
    print(f"    OpenStreetMap located {found:,} of the {done:,} asked")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    ap.add_argument("--fresh", action="store_true",
                     help="send every address again, ignoring what was answered before")
    ap.add_argument("--batch-size", type=int, default=MAX_BATCH)
    ap.add_argument("--benchmark", default=BENCHMARK)
    ap.add_argument("--limit", type=int, default=None,
                     help="cap each geocoder's list of addresses (smoke-testing)")
    ap.add_argument("--census-response-file", default=None,
                     help="skip the network call; treat this file's contents as "
                          "Census's response CSV (hermetic testing — only "
                          "meaningful with a candidate list that fits one batch). "
                          "OpenStreetMap is then asked only from --osm-response-file")
    ap.add_argument("--osm-response-file", default=None,
                     help="skip the network call; JSON of OpenStreetMap's answers by "
                          "'street|city|zip' (hermetic testing)")
    args = ap.parse_args()

    nd = store_dir(args.data_dir, args.test, "nppes")
    rd = store_dir(args.data_dir, args.test, "reference")
    ga_path = f"{nd}/ga_providers.parquet"
    seen_path = f"{rd}/geocode_addresses.parquet"

    if not os.path.exists(ga_path):
        raise SystemExit(f"{ga_path} not found -- run `make reference STEP=nppes` first")

    con = duckdb.connect()

    # Billing IDs an MRF names. A TIN that isn't an NPI has no NPPES address to find.
    providers = f"{raw_dir(args.data_dir, args.test, 'anthem')}/providers/*.parquet"
    if glob.glob(providers):
        con.execute(f"""CREATE TEMP TABLE billing_npis AS
            SELECT DISTINCT TRY_CAST(tin_value AS BIGINT) AS npi
            FROM read_parquet('{providers}', union_by_name=true)""")
    else:
        con.execute("CREATE TEMP TABLE billing_npis (npi BIGINT)")
    billing = "npi IN (SELECT npi FROM billing_npis)"

    con.execute(f"CREATE TEMP TABLE seen ({SEEN_COLUMNS})")
    if os.path.exists(seen_path) and not args.fresh:
        con.execute(f"INSERT INTO seen BY NAME SELECT * FROM read_parquet('{seen_path}')")

    # Every address to place, once. `place` says a biller or a hospital is there,
    # which is what goes on the map; a clinician's address only has to be
    # recognized as one of those buildings, which Census's answer is enough for.
    # Each source after the first is optional, since its step may not have run.
    sources = [f"""SELECT address_line1, COALESCE(address_line2, '') AS address_line2, city, state,
                          postal_code, true AS place
                   FROM read_parquet('{ga_path}') WHERE {billing}"""]
    locations = f"{nd}/ga_locations.parquet"
    if os.path.exists(locations):
        sources.append(f"""SELECT address_line1, COALESCE(address_line2, ''), city, state, postal_code, true
                           FROM read_parquet('{locations}') WHERE {billing}""")
    hospitals = f"{rd}/hospitals_ga.parquet"
    n_hosp = 0
    if os.path.exists(hospitals):
        n_hosp = con.execute(f"SELECT count(*) FROM read_parquet('{hospitals}')").fetchone()[0]
        sources.append(f"SELECT address, '', city, state, postal_code, true FROM read_parquet('{hospitals}')")
    clinicians = f"{rd}/dac_locations_ga.parquet"
    if os.path.exists(clinicians):
        sources.append(f"""SELECT address_line1, COALESCE(address_line2, ''), city, state, postal_code, false
                           FROM read_parquet('{clinicians}')""")
    con.execute(f"""
        CREATE TEMP TABLE candidates AS
        SELECT {ADDRESS_KEY}, bool_or(place) AS place
        FROM ({" UNION ALL ".join(sources)})
        WHERE address_line1 IS NOT NULL AND address_line1 <> ''
          AND postal_code IS NOT NULL AND postal_code <> ''
        GROUP BY {ADDRESS_KEY}
    """)
    # Only addresses Census hasn't answered for. row_number() gives each one an
    # id for the batch CSV, which Census echoes back.
    con.execute(f"""
        CREATE TEMP TABLE unseen AS
        SELECT ROW_NUMBER() OVER (ORDER BY {ADDRESS_KEY}) AS addr_id, *
        FROM (SELECT {ADDRESS_KEY} FROM candidates
              EXCEPT SELECT {ADDRESS_KEY} FROM seen WHERE source = 'census')
        ORDER BY addr_id
        {f'LIMIT {args.limit}' if args.limit else ''}
    """)
    n_npi, n_addr, n_place, n_new = con.execute(f"""
        SELECT count(*), (SELECT count(*) FROM candidates), (SELECT count(*) FROM candidates WHERE place),
               (SELECT count(*) FROM unseen)
        FROM read_parquet('{ga_path}')
        WHERE {billing}
    """).fetchone()
    print(f"→ {n_npi:,} billing IDs and {n_hosp:,} hospitals at {n_place:,} addresses, "
          f"{n_addr:,} with clinicians' -> {n_new:,} not sent to Census before")
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
            SELECT {ADDRESS_KEY}, 'census', match_type, matched_address, latitude, longitude, {LOCATED}, ?, ?
            FROM unseen u JOIN answered a USING (addr_id)
        """, [args.benchmark, today])
        # Saved after each batch, so a failure part-way loses at most one batch.
        _save(con, seen_path)

    if n_batches == 0:
        if args.fresh or not os.path.exists(seen_path):
            _save(con, seen_path)
        print("  nothing new to send to Census")

    # A canned Census answer means a hermetic run, which mustn't reach the network.
    if args.osm_response_file or not args.census_response_file:
        _ask_openstreetmap(con, seen_path, today, args.limit, args.osm_response_file)

    n_census, n_osm, n_left = con.execute(f"""
        SELECT count(*) FILTER (WHERE by_census), count(*) FILTER (WHERE by_osm AND NOT by_census),
               count(*) FILTER (WHERE place AND NOT by_census AND NOT by_osm)
        FROM (SELECT c.place,
                     coalesce(bool_or(s.located) FILTER (WHERE s.source = 'census'), false) AS by_census,
                     coalesce(bool_or(s.located) FILTER (WHERE s.source = 'openstreetmap'), false) AS by_osm
              FROM candidates c LEFT JOIN seen s USING ({ADDRESS_KEY})
              GROUP BY {ADDRESS_KEY}, c.place)
    """).fetchone()
    if n_census == 0:
        raise SystemExit("Census geocoder located nothing -- check connectivity / API status")
    print(f"→ wrote {seen_path}")
    print(f"  {n_census:,}/{n_addr:,} addresses located by Census ({100 * n_census / n_addr:.1f}%), "
          f"{n_osm:,} more by OpenStreetMap; {n_left:,} of billers' and hospitals' addresses not located")


if __name__ == "__main__":
    main()
