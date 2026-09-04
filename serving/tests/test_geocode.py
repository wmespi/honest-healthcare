"""Hermetic test for the geocode builder (reference/geocode.py).

No network — runs the builder against a tiny committed NPPES fixture, with
--census-response-file substituting for the real Census batch call (a canned
response, reference/testdata/geocode_census_response_sample.csv). Test
isolation (--test): writes data-test/, never data/. Picked up by
`make test-api` and `make check-local`.
"""
import os
import subprocess
import sys

import duckdb
import pytest

# Repo-root relative so this runs on the host (`make check-local`) too; in the
# container REPO resolves to /app. GH #59.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CENSUS_RESPONSE = os.path.join(REPO, "reference/testdata/geocode_census_response_sample.csv")
NPPES_FIXTURE = os.path.join(REPO, "data-test/nppes/ga_providers.parquet")
OUT = os.path.join(REPO, "data-test/reference/pcp_geocode.parquet")


def _write_nppes_fixture():
    """Five NPPES rows exercising every path the builder needs to get right:
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
    """
    os.makedirs(os.path.dirname(NPPES_FIXTURE), exist_ok=True)
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
    ]
    body = ", ".join(
        "(" + ", ".join(f"'{c}'" if isinstance(c, str) else str(c) for c in r) + ")"
        for r in rows
    )
    con.execute(f"""
        COPY (SELECT * FROM (VALUES {body}) AS t(
            npi, entity_type, org_name, last_name, first_name, taxonomy_code, taxonomy_group,
            address_line1, address_line2, city, state, postal_code
        )) TO '{NPPES_FIXTURE}' (FORMAT parquet)
    """)


@pytest.fixture(scope="module")
def built():
    _write_nppes_fixture()
    r = subprocess.run(
        [sys.executable, "-m", "reference.geocode",
         "--census-response-file", CENSUS_RESPONSE, "--test"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    yield duckdb.connect()
    for p in (OUT, NPPES_FIXTURE):
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
    assert set(rows) == {2000000001, 2000000002}


def test_shared_address_dedups_to_one_geocode_call_same_coords(built):
    # Both NPIs at "100 Main St" get the exact same matched coordinate — proof
    # the dedup-then-rejoin path (not a per-NPI lookup) is what ran.
    rows = {r[0]: (r[1], r[2]) for r in
            built.execute(f"SELECT npi, latitude, longitude FROM read_parquet('{OUT}')").fetchall()}
    assert rows[2000000001] == rows[2000000002] == (32.8407, -83.6324)


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
