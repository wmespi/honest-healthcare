"""Hermetic test for the geocode builder (ingestion/reference/geocode.py).

No network — runs the builder against a tiny committed NPPES fixture, with
--census-response-file substituting for the real Census batch call (a canned
response, ingestion/reference/testdata/geocode_census_response_sample.csv). Test
isolation (--test): writes data-test/, never data/. Picked up by
`make test`.
"""
import json
import os
import subprocess
import sys

import duckdb
import pytest

# Repo-root relative so this runs on the host (`make test LOCAL=1`) too; in the
# container REPO resolves to /app. GH #59.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CENSUS_RESPONSE = os.path.join(REPO, "ingestion/reference/testdata/geocode_census_response_sample.csv")
NPPES_FIXTURE = os.path.join(REPO, "data-test/ingestion/nppes/ga_providers.parquet")
PROVIDERS_FIXTURE = os.path.join(REPO, "data-test/raw/anthem/providers/geocode_fixture.parquet")
SEEN = os.path.join(REPO, "data-test/ingestion/reference/geocode_addresses.parquet")
# Another builder's test output; the geocoder would send its addresses too.
HOSPITALS = os.path.join(REPO, "data-test/ingestion/reference/hospitals_ga.parquet")
# The billing IDs in the MRF fixture. A TIN that isn't an NPI has no NPPES record,
# and mustn't break the lookup.
BILLING = ("2000000001", "2000000002", "2000000003", "2000000005", "2000000006", "58-1234567")
COLUMNS = ("npi, entity_type, org_name, last_name, first_name, taxonomy_code, taxonomy_group, "
           "address_line1, address_line2, city, state, postal_code")


def _write_nppes_fixture(nppes=NPPES_FIXTURE, providers=PROVIDERS_FIXTURE, extra=(), billing=()):
    """Six NPPES rows, five of them billing IDs in an MRF:
      2000000001 / 2000000006 — share one address (Macon), sent to Census once.
      2000000003 — an address (Athens) the canned Census response marks No_Match,
        kept with an empty answer so it isn't sent again.
      2000000004 — not a billing ID, so its address is never sent.
      2000000005 — an address the canned response matches in Maryland, kept as
        Census gave it (the build counts it as no match).
      2000000002 — a billing ID at the Macon address too.
    """
    os.makedirs(os.path.dirname(nppes), exist_ok=True)
    con = duckdb.connect()
    rows = [
        (2000000001, "individual", "", "Alpha", "Anna", "207R00000X", "Internal Medicine",
         "100 Main St", "", "Macon", "GA", "31201"),
        (2000000002, "individual", "", "Beta", "Bo", "363LF0000X", "Nurse Practitioner",
         "100 Main St", "", "Macon", "GA", "31201"),
        (2000000003, "individual", "", "Gamma", "Gia", "208D00000X", "General Practice",
         "200 Oak Ave", "", "Athens", "GA", "30601"),
        (2000000004, "individual", "", "Delta", "Dan", "207RC0000X", "Cardiovascular Disease",
         "300 Heart Way", "", "Savannah", "GA", "31401"),
        (2000000005, "individual", "", "Epsilon", "Eve", "207Q00000X", "Family Medicine",
         "400 Wrong State Blvd", "", "Somewhere", "GA", "39999"),
        (2000000006, "organization", "Macon Multi-Specialty", "", "", "193200000X", "Multi-Specialty",
         "100 Main St", "", "Macon", "GA", "31201"),
        *extra,
    ]
    body = ", ".join(
        "(" + ", ".join(f"'{c}'" if isinstance(c, str) else str(c) for c in r) + ")"
        for r in rows
    )
    con.execute(f"""
        COPY (SELECT * FROM (VALUES {body}) AS t({COLUMNS})) TO '{nppes}' (FORMAT parquet)
    """)
    os.makedirs(os.path.dirname(providers), exist_ok=True)
    con.execute(f"""
        COPY (SELECT * FROM (VALUES {", ".join(f"('{t}')" for t in BILLING + tuple(billing))}) AS t(tin_value))
        TO '{providers}' (FORMAT parquet)
    """)


@pytest.fixture(scope="module")
def built():
    for p in (SEEN, HOSPITALS):
        if os.path.exists(p):
            os.remove(p)
    _write_nppes_fixture()
    r = subprocess.run(
        [sys.executable, "-m", "ingestion.reference.geocode",
         "--census-response-file", CENSUS_RESPONSE, "--test"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    yield duckdb.connect()
    for p in (SEEN, NPPES_FIXTURE, PROVIDERS_FIXTURE):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def test_every_address_sent_is_kept_with_censuss_answer(built):
    rows = built.execute(f"""SELECT address_line1, source, match_type, matched_address, latitude, located
                             FROM read_parquet('{SEEN}') ORDER BY 1""").fetchall()
    assert rows == [
        ("100 Main St", "census", "Exact", "100 MAIN ST, MACON, GA, 31201", 32.8407, True),
        ("200 Oak Ave", "census", None, None, None, False),  # No_Match, kept so it isn't sent again
        # Census's answer is kept as it gave it, and isn't a point in Georgia.
        ("400 Wrong State Blvd", "census", "Exact", "400 WRONG STATE BLVD, SOMEWHERE, GA, 39999", 38.9612, False),
    ]
    # 300 Heart Way isn't a billing ID's address, and 100 Main St went once for three.


def test_only_addresses_census_hasnt_answered_are_sent(tmp_path):
    data = tmp_path / "data"
    nppes = str(data / "ingestion/nppes/ga_providers.parquet")
    providers = str(data / "raw/anthem/providers/fixture.parquet")
    seen = data / "ingestion/reference/geocode_addresses.parquet"

    def run(response, *extra):
        r = subprocess.run([sys.executable, "-m", "ingestion.reference.geocode", "--data-dir", str(data),
                            "--census-response-file", str(response), *extra],
                           cwd=REPO, capture_output=True, text=True)
        assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
        rows = duckdb.connect().execute(f"SELECT * EXCLUDE (geocoded_on) FROM read_parquet('{seen}') ORDER BY ALL").fetchall()
        return r.stdout, rows

    _write_nppes_fixture(nppes, providers)
    _, first = run(CENSUS_RESPONSE)
    assert len(first) == 3

    # Nothing new, so no call: a response file that doesn't exist would fail if read.
    stdout, again = run(tmp_path / "absent.csv")
    assert "nothing new to send to Census" in stdout and again == first

    _, fresh = run(CENSUS_RESPONSE, "--fresh")
    assert fresh == first

    # One new billing ID's address, which is all Census is sent (as id 1).
    _write_nppes_fixture(nppes, providers, billing=["2000000007"], extra=[
        (2000000007, "individual", "", "Zeta", "Zoe", "207Q00000X", "Family Medicine",
         "500 Pine Rd", "", "Rome", "GA", "30161")])
    new = tmp_path / "new.csv"
    new.write_text('"1","500 Pine Rd, Rome, GA, 30161","Match","Exact","500 PINE RD, ROME, GA, 30161",'
                   '"-85.1647,34.2570","1","L"\n')
    stdout, added = run(new)
    assert "1 not sent to Census before" in stdout
    assert len(added) == 4 and ("500 Pine Rd", "", "Rome", "GA", "30161", "census", "Exact",
                                "500 PINE RD, ROME, GA, 30161", 34.257, -85.1647, True,
                                "Public_AR_Current") in added

    # A hospital's own address is sent too.
    duckdb.connect().execute(f"""
        COPY (SELECT '110001' AS ccn, '1200 MEMORIAL DRIVE' AS address, 'DALTON' AS city,
                     'GA' AS state, '30720' AS postal_code)
        TO '{data}/ingestion/reference/hospitals_ga.parquet' (FORMAT parquet)""")
    hospital = tmp_path / "hospital.csv"
    hospital.write_text('"1","1200 MEMORIAL DRIVE, DALTON, GA, 30720","Match","Non_Exact",'
                        '"1200 MEMORIAL DR, DALTON, GA, 30720","-84.9786,34.7690","1","L"\n')
    stdout, after = run(hospital)
    assert "1 hospitals" in stdout and "1 not sent to Census before" in stdout
    assert len(after) == 5
    assert duckdb.connect().execute(
        f"SELECT matched_address FROM read_parquet('{seen}') WHERE address_line1 = '1200 MEMORIAL DRIVE'"
    ).fetchone() == ("1200 MEMORIAL DR, DALTON, GA, 30720",)


def test_openstreetmap_is_asked_for_the_places_census_couldnt_put_in_georgia(tmp_path):
    data = tmp_path / "data"
    nppes = str(data / "ingestion/nppes/ga_providers.parquet")
    providers = str(data / "raw/anthem/providers/fixture.parquet")
    seen = data / "ingestion/reference/geocode_addresses.parquet"
    # A biller whose street has no house number, a biller's secondary location,
    # and an address only a clinician is listed at.
    _write_nppes_fixture(nppes, providers, billing=["2000000008"], extra=[
        (2000000008, "organization", "Plaza Imaging", "", "", "261QR0200X", "Radiology Clinic",
         "One Hospital Plaza", "", "Albany", "GA", "31701")])
    con = duckdb.connect()
    con.execute(f"""
        COPY (SELECT 2000000003 AS npi, '4700 Waters Ave' AS address_line1, 'STE 200' AS address_line2,
                     'Savannah' AS city, 'GA' AS state, '314046220' AS postal_code, '9125550134' AS phone)
        TO '{data}/ingestion/nppes/ga_locations.parquet' (FORMAT parquet)""")
    os.makedirs(data / "ingestion/reference")
    con.execute(f"""
        COPY (SELECT 2000000004 AS npi, '55 Tenth St' AS address_line1, '' AS address_line2,
                     'Atlanta' AS city, 'GA' AS state, '30309' AS postal_code, NULL::VARCHAR AS phone)
        TO '{data}/ingestion/reference/dac_locations_ga.parquet' (FORMAT parquet)""")
    census = tmp_path / "census.csv"
    census.write_text(
        '"1","100 Main St, Macon, GA, 31201","Match","Exact","100 MAIN ST, MACON, GA, 31201","-83.6324,32.8407","1","L"\n'
        '"2","200 Oak Ave, Athens, GA, 30601","No_Match"\n'
        '"3","400 Wrong State Blvd, Somewhere, GA, 39999","Match","Exact",'
        '"400 WRONG STATE BLVD, SOMEWHERE, GA, 39999","-76.9000,38.9612","1","L"\n'
        '"4","4700 Waters Ave STE 200, Savannah, GA, 314046220","No_Match"\n'
        '"5","55 Tenth St, Atlanta, GA, 30309","No_Match"\n'
        '"6","One Hospital Plaza, Albany, GA, 31701","No_Match"\n')
    osm = tmp_path / "osm.json"
    osm.write_text(json.dumps({
        # The building itself: kept as a point.
        "200 Oak Ave|Athens|30601": [
            {"lat": "33.9601", "lon": "-83.3771", "addresstype": "building",
             "address": {"house_number": "200", "road": "Oak Avenue", "city": "Athens",
                         "state": "Georgia", "postcode": "30601"}}],
        # Only the street: where the address roughly is, so no match.
        "400 Wrong State Blvd|Somewhere|39999": [
            {"lat": "31.5", "lon": "-83.5", "addresstype": "road",
             "address": {"road": "Wrong State Boulevard", "state": "Georgia", "postcode": "39999"}}],
    }))

    def run(census_file, osm_file):
        r = subprocess.run([sys.executable, "-m", "ingestion.reference.geocode", "--data-dir", str(data),
                            "--census-response-file", str(census_file), "--osm-response-file", str(osm_file)],
                           cwd=REPO, capture_output=True, text=True)
        assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
        return r.stdout, con.execute(f"""
            SELECT address_line1, source, match_type, latitude, longitude, located
            FROM read_parquet('{seen}') ORDER BY source, address_line1""").fetchall()

    stdout, rows = run(census, osm)
    # Census is sent all six, the secondary location and the clinician's address included.
    assert [(r[0], r[5]) for r in rows if r[1] == "census"] == [
        ("100 Main St", True), ("200 Oak Ave", False), ("400 Wrong State Blvd", False),
        ("4700 Waters Ave", False), ("55 Tenth St", False), ("One Hospital Plaza", False)]
    # OpenStreetMap is asked for the three with a house number where a biller is,
    # with the suite dropped and a five-digit ZIP code, and each answer is kept.
    assert "3 to ask OpenStreetMap, 1 with no house number" in stdout
    assert [r for r in rows if r[1] == "openstreetmap"] == [
        ("200 Oak Ave", "openstreetmap", "building", 33.9601, -83.3771, True),
        ("400 Wrong State Blvd", "openstreetmap", None, None, None, False),
        ("4700 Waters Ave", "openstreetmap", None, None, None, False),
    ]

    # Nothing new for either, so neither file is read: one that doesn't exist would fail.
    stdout, again = run(tmp_path / "absent.csv", tmp_path / "absent.json")
    assert "0 to ask OpenStreetMap" in stdout and again == rows
