"""Hermetic test for the geocode builder (reference/geocode.py).

No network — runs the builder against a tiny committed NPPES fixture, with
--census-response-file substituting for the real Census batch call (a canned
response, reference/testdata/geocode_census_response_sample.csv). Test
isolation (--test): writes data-test/, never data/. Picked up by
`make test`.
"""
import os
import subprocess
import sys

import duckdb
import pytest

# Repo-root relative so this runs on the host (`make test LOCAL=1`) too; in the
# container REPO resolves to /app. GH #59.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CENSUS_RESPONSE = os.path.join(REPO, "reference/testdata/geocode_census_response_sample.csv")
NPPES_FIXTURE = os.path.join(REPO, "data-test/nppes/ga_providers.parquet")
PROVIDERS_FIXTURE = os.path.join(REPO, "data-test/anthem/providers/geocode_fixture.parquet")
OUT = os.path.join(REPO, "data-test/reference/pcp_geocode.parquet")
SEEN = os.path.join(REPO, "data-test/reference/geocode_addresses.parquet")
# Another builder's test output; the geocoder would send its addresses too.
HOSPITALS = os.path.join(REPO, "data-test/reference/hospitals_ga.parquet")
COLUMNS = ("npi, entity_type, org_name, last_name, first_name, taxonomy_code, taxonomy_group, "
           "address_line1, address_line2, city, state, postal_code")


def _write_nppes_fixture(nppes=NPPES_FIXTURE, providers=PROVIDERS_FIXTURE, extra=()):
    """Six NPPES rows exercising every path the builder needs to get right:
      2000000001 / 2000000002 — both PCP-eligible, share one address (Macon) —
        the dedup + rejoin (both must land on the same, single-geocoded coord).
      2000000003 — PCP-eligible, a *different* address (Athens) that the
        canned Census response marks No_Match — must be absent from the
        output, not zeroed.
      2000000004 — NOT PCP-eligible (cardiologist taxonomy) — must never reach
        the candidate list at all, regardless of its address.
      2000000005 — PCP-eligible, at an address the canned Census response
        marks Match but geocodes to Maryland — must be absent from the
        output despite the Match status (the GA bounding-box filter).
      2000000006 — NOT PCP-eligible, but a billing ID in an MRF — a candidate
        anyway, because a practice's distance comes from its own address.
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
    # A TIN that isn't an NPI has no NPPES record, and mustn't break the lookup.
    os.makedirs(os.path.dirname(providers), exist_ok=True)
    con.execute(f"""
        COPY (SELECT * FROM (VALUES ('2000000006'), ('58-1234567')) AS t(tin_value))
        TO '{providers}' (FORMAT parquet)
    """)


@pytest.fixture(scope="module")
def built():
    for p in (OUT, SEEN, HOSPITALS):
        if os.path.exists(p):
            os.remove(p)
    _write_nppes_fixture()
    r = subprocess.run(
        [sys.executable, "-m", "reference.geocode",
         "--census-response-file", CENSUS_RESPONSE, "--test"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    yield duckdb.connect()
    for p in (OUT, SEEN, NPPES_FIXTURE, PROVIDERS_FIXTURE):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def test_schema(built):
    cols = {r[0]: r[1] for r in built.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{OUT}')").fetchall()}
    assert set(cols) == {"npi", "latitude", "longitude"}
    assert cols["latitude"] == "DOUBLE" and cols["longitude"] == "DOUBLE"


def test_matched_addresses_geocoded(built):
    rows = {r[0]: (r[1], r[2]) for r in
            built.execute(f"SELECT npi, latitude, longitude FROM read_parquet('{OUT}')").fetchall()}
    # 2000000003 (No_Match), 2000000004 (not PCP), 2000000005 (matched but
    # outside Georgia) all absent
    assert set(rows) == {2000000001, 2000000002, 2000000006}


def test_shared_address_dedups_to_one_geocode_call_same_coords(built):
    # Both NPIs at "100 Main St" get the exact same matched coordinate — proof
    # the dedup-then-rejoin path (not a per-NPI lookup) is what ran.
    rows = {r[0]: (r[1], r[2]) for r in
            built.execute(f"SELECT npi, latitude, longitude FROM read_parquet('{OUT}')").fetchall()}
    assert rows[2000000001] == rows[2000000002] == (32.8407, -83.6324)


def test_billing_id_is_a_candidate_whatever_its_taxonomy(built):
    rows = built.execute(f"SELECT latitude, longitude FROM read_parquet('{OUT}') WHERE npi = 2000000006").fetchall()
    assert rows == [(32.8407, -83.6324)]


def test_non_pcp_taxonomy_never_a_candidate(built):
    rows = built.execute(f"SELECT npi FROM read_parquet('{OUT}') WHERE npi = 2000000004").fetchall()
    assert rows == []


def test_unmatched_address_is_absent_not_zeroed(built):
    rows = built.execute(f"SELECT npi FROM read_parquet('{OUT}') WHERE npi = 2000000003").fetchall()
    assert rows == []


def test_matched_but_outside_georgia_is_dropped(built):
    # Census marked this one "Match" — the bounding-box filter, not the
    # match-status check, is what has to catch it.
    rows = built.execute(f"SELECT npi FROM read_parquet('{OUT}') WHERE npi = 2000000005").fetchall()
    assert rows == []


def test_every_address_sent_is_kept_with_censuss_answer(built):
    rows = built.execute(f"""SELECT address_line1, match_type, matched_address, latitude
                             FROM read_parquet('{SEEN}') ORDER BY 1""").fetchall()
    assert rows == [
        ("100 Main St", "Exact", "100 MAIN ST, MACON, GA, 31201", 32.8407),
        ("200 Oak Ave", None, None, None),  # No_Match, kept so it isn't sent again
        ("400 Wrong State Blvd", "Exact", "400 WRONG STATE BLVD, SOMEWHERE, GA, 39999", 38.9612),
    ]


def test_only_addresses_census_hasnt_answered_are_sent(tmp_path):
    data = tmp_path / "data"
    nppes = str(data / "nppes/ga_providers.parquet")
    providers = str(data / "anthem/providers/fixture.parquet")
    out = data / "reference/pcp_geocode.parquet"
    seen = data / "reference/geocode_addresses.parquet"

    def run(response, *extra):
        r = subprocess.run([sys.executable, "-m", "reference.geocode", "--data-dir", str(data),
                            "--census-response-file", str(response), *extra],
                           cwd=REPO, capture_output=True, text=True)
        assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
        rows = duckdb.connect().execute(f"SELECT * FROM read_parquet('{out}') ORDER BY npi").fetchall()
        n_seen = duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{seen}')").fetchone()[0]
        return r.stdout, rows, n_seen

    _write_nppes_fixture(nppes, providers)
    _, first, n_first = run(CENSUS_RESPONSE)
    assert n_first == 3

    # Nothing new, so no call: a response file that doesn't exist would fail if read.
    stdout, again, n_again = run(tmp_path / "absent.csv")
    assert "nothing new to send to Census" in stdout
    assert (again, n_again) == (first, n_first)

    _, fresh, n_fresh = run(CENSUS_RESPONSE, "--fresh")
    assert (fresh, n_fresh) == (first, n_first)

    # One new address, which is all Census is sent (as id 1).
    _write_nppes_fixture(nppes, providers, extra=[
        (2000000007, "individual", "", "Zeta", "Zoe", "207Q00000X", "Family Medicine",
         "500 Pine Rd", "", "Rome", "GA", "30161")])
    new = tmp_path / "new.csv"
    new.write_text('"1","500 Pine Rd, Rome, GA, 30161","Match","Exact","500 PINE RD, ROME, GA, 30161",'
                   '"-85.1647,34.2570","1","L"\n')
    stdout, added, n_added = run(new)
    assert "1 not sent to Census before" in stdout
    assert n_added == 4 and added == first + [(2000000007, 34.257, -85.1647)]

    # A hospital's own address is sent too, and leaves the NPI coordinates alone.
    duckdb.connect().execute(f"""
        COPY (SELECT '110001' AS ccn, '1200 MEMORIAL DRIVE' AS address, 'DALTON' AS city,
                     'GA' AS state, '30720' AS postal_code)
        TO '{data}/reference/hospitals_ga.parquet' (FORMAT parquet)""")
    hospital = tmp_path / "hospital.csv"
    hospital.write_text('"1","1200 MEMORIAL DRIVE, DALTON, GA, 30720","Match","Non_Exact",'
                        '"1200 MEMORIAL DR, DALTON, GA, 30720","-84.9786,34.7690","1","L"\n')
    stdout, after, n_after = run(hospital)
    assert "1 hospitals" in stdout and "1 not sent to Census before" in stdout
    assert n_after == 5 and after == added
    assert duckdb.connect().execute(
        f"SELECT matched_address FROM read_parquet('{seen}') WHERE address_line1 = '1200 MEMORIAL DRIVE'"
    ).fetchone() == ("1200 MEMORIAL DR, DALTON, GA, 30720",)

