"""Hermetic test for the Doctors & Clinicians builder (reference/doctors_clinicians.py).

No network, no live API — runs the builder against a committed CSV fixture in
test isolation (writes data-test/reference/, never data/reference/). The fixture
carries the National file's wide ``hosp_afl_*`` columns, so ``--dac-file`` alone
produces both outputs offline.
"""
import os
import subprocess
import sys

import duckdb
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "reference", "testdata", "dac_sample.csv")
GA_OUT = os.path.join(REPO, "data-test", "reference", "dac_ga.parquet")
AFFIL_OUT = os.path.join(REPO, "data-test", "reference", "dac_hospital_affiliations.parquet")
NPI_LOOKUP = os.path.join(REPO, "data-test", "anthem", "npi_lookup.parquet")


def _rm(*paths):
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


@pytest.fixture(scope="module")
def built():
    # no npi_lookup → the builder's State='GA' geo branch (see built_npi_scoped
    # for the other one). Clear any leftover so this is hermetic.
    _rm(NPI_LOOKUP)
    r = subprocess.run(
        [sys.executable, "-m", "reference.doctors_clinicians",
         "--dac-file", FIXTURE, "--test"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    assert "State = 'GA'" in r.stdout, r.stdout
    yield duckdb.connect()
    _rm(GA_OUT, AFFIL_OUT)


def test_dac_ga_one_row_per_npi_georgia_only(built):
    # fixture: 9 valid GA clinicians + 2 out-of-state + 1 corrupt NPI
    rows = built.execute(f"SELECT count(*), count(DISTINCT npi) FROM read_parquet('{GA_OUT}')").fetchone()
    assert rows == (9, 9)
    states = built.execute(
        f"SELECT count(*) FROM read_parquet('{GA_OUT}') WHERE npi IN (2000000001, 2000000002)"
    ).fetchone()[0]
    assert states == 0


def test_dac_ga_schema(built):
    cols = {r[0]: r[1] for r in built.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{GA_OUT}')").fetchall()}
    assert set(cols) == {
        "npi", "last_name", "first_name", "credential", "primary_specialty",
        "org_pac_id", "org_name", "grad_year", "med_school", "gender",
    }
    assert cols["npi"] == "BIGINT"
    assert cols["grad_year"] == "INTEGER"


def test_dac_ga_picks_primary_group(built):
    # NPI ...01 appears twice under group 1111111111 and once under 2222222222 —
    # the more-frequent group wins.
    row = built.execute(f"""
        SELECT org_pac_id, org_name, grad_year, gender, credential
        FROM read_parquet('{GA_OUT}') WHERE npi = 1000000001
    """).fetchone()
    assert row == ("1111111111", "Peachtree Internal Medicine LLC", 2005, "F", "M.D.")


def test_dac_ga_keeps_solo_and_null_grad_year(built):
    # solo clinician (blank org_pac_id) is still kept
    solo = built.execute(
        f"SELECT org_pac_id, org_name FROM read_parquet('{GA_OUT}') WHERE npi = 1000000006"
    ).fetchone()
    assert solo == (None, None)
    # blank Grd_yr → NULL, not 0
    gy = built.execute(
        f"SELECT grad_year FROM read_parquet('{GA_OUT}') WHERE npi = 3000000001"
    ).fetchone()[0]
    assert gy is None


def test_hospital_affiliations_bridge(built):
    cols = {r[0] for r in built.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{AFFIL_OUT}')").fetchall()}
    assert cols == {"npi", "ccn", "facility_name"}
    # NPI ...01 has two hospitals across its rows; dedup keeps both
    afl = built.execute(f"""
        SELECT ccn, facility_name FROM read_parquet('{AFFIL_OUT}')
        WHERE npi = 1000000001 ORDER BY ccn
    """).fetchall()
    assert afl == [("110001", "Emory University Hospital"),
                   ("110002", "Grady Memorial Hospital")]


def test_hospital_affiliations_scoped_to_dac_ga(built):
    # the out-of-state clinician's Orlando affiliation must not leak in
    leaked = built.execute(
        f"SELECT count(*) FROM read_parquet('{AFFIL_OUT}') WHERE npi = 2000000001"
    ).fetchone()[0]
    assert leaked == 0
    # every affiliation NPI is present in dac_ga
    orphans = built.execute(f"""
        SELECT count(*) FROM read_parquet('{AFFIL_OUT}') a
        WHERE a.npi NOT IN (SELECT npi FROM read_parquet('{GA_OUT}'))
    """).fetchone()[0]
    assert orphans == 0


@pytest.fixture(scope="module")
def built_npi_scoped():
    """Exercise the `npi IN npi_lookup` geo branch — the production path, and
    where the National file's all-varchar NPI vs npi_lookup's BIGINT bit us
    (a real-data-only BinderException the State='GA' fixture path never hit)."""
    os.makedirs(os.path.dirname(NPI_LOOKUP), exist_ok=True)
    try:
        con = duckdb.connect()
        # 3 GA fixture NPIs + one OUT of state but contracted (must be kept —
        # the scope is npi_lookup, not State).
        con.execute(f"""
            COPY (SELECT * FROM (VALUES
                (1000000001, '1000000010'), (1000000002, '1000000010'),
                (1000000003, '1000000010'), (2000000001, '9000000001')
            ) AS t(npi, tin_value)) TO '{NPI_LOOKUP}' (FORMAT parquet)
        """)
        con.close()
        r = subprocess.run(
            [sys.executable, "-m", "reference.doctors_clinicians",
             "--dac-file", FIXTURE, "--test"],
            cwd=REPO, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
        assert "NPIs in" in r.stdout, f"expected the npi_lookup geo branch:\n{r.stdout}"
        yield duckdb.connect()
    finally:
        _rm(GA_OUT, AFFIL_OUT, NPI_LOOKUP)


def test_geo_scope_is_npi_lookup_not_state(built_npi_scoped):
    npis = {r[0] for r in built_npi_scoped.execute(
        f"SELECT npi FROM read_parquet('{GA_OUT}')").fetchall()}
    assert npis == {1000000001, 1000000002, 1000000003, 2000000001}
    # out-of-state NPI kept because it's in npi_lookup; a GA clinician NOT in
    # npi_lookup is dropped
    assert 2000000001 in npis and 1000000004 not in npis
