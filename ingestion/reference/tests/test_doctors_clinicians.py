"""Hermetic test for the Doctors & Clinicians builder (ingestion/reference/doctors_clinicians.py).

No network, no live API — runs the builder against a committed CSV fixture with
its own temporary data dir, so ``--dac-file`` alone produces the output offline
and nothing under data/ or data-test/ is read or written.
"""
import os
import subprocess
import sys

import duckdb
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
FIXTURE = os.path.join(REPO, "ingestion", "reference", "testdata", "dac_sample.csv")


def _build(data_dir):
    """Run the builder over the fixture into `data_dir`; returns its stdout and
    the path of dac_ga.parquet."""
    r = subprocess.run(
        [sys.executable, "-m", "ingestion.reference.doctors_clinicians",
         "--dac-file", FIXTURE, "--data-dir", str(data_dir)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    return r.stdout, os.path.join(data_dir, "ingestion", "reference", "dac_ga.parquet")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    # No parsed rosters, so the builder's State='GA' geo branch (see
    # built_roster_scoped for the other one).
    stdout, out = _build(tmp_path_factory.mktemp("data"))
    assert "State = 'GA'" in stdout, stdout
    return duckdb.connect(), out


def test_dac_ga_one_row_per_npi_georgia_only(built):
    con, out = built
    # fixture: 9 valid GA clinicians + 2 out-of-state + 1 corrupt NPI
    rows = con.execute(f"SELECT count(*), count(DISTINCT npi) FROM read_parquet('{out}')").fetchone()
    assert rows == (9, 9)
    states = con.execute(
        f"SELECT count(*) FROM read_parquet('{out}') WHERE npi IN (2000000001, 2000000002)"
    ).fetchone()[0]
    assert states == 0


def test_dac_ga_schema(built):
    con, out = built
    cols = {r[0]: r[1] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{out}')").fetchall()}
    assert set(cols) == {
        "npi", "last_name", "first_name", "credential", "primary_specialty",
        "org_pac_id", "org_name", "grad_year", "med_school", "gender",
    }
    assert cols["npi"] == "BIGINT"
    assert cols["grad_year"] == "INTEGER"


def test_dac_ga_picks_primary_group(built):
    con, out = built
    # NPI ...01 appears twice under group 1111111111 and once under 2222222222 —
    # the more-frequent group wins.
    row = con.execute(f"""
        SELECT org_pac_id, org_name, grad_year, gender, credential
        FROM read_parquet('{out}') WHERE npi = 1000000001
    """).fetchone()
    assert row == ("1111111111", "Peachtree Internal Medicine LLC", 2005, "F", "M.D.")


def test_dac_ga_keeps_solo_and_null_grad_year(built):
    con, out = built
    # solo clinician (blank org_pac_id) is still kept
    solo = con.execute(
        f"SELECT org_pac_id, org_name FROM read_parquet('{out}') WHERE npi = 1000000006"
    ).fetchone()
    assert solo == (None, None)
    # blank Grd_yr → NULL, not 0
    gy = con.execute(
        f"SELECT grad_year FROM read_parquet('{out}') WHERE npi = 3000000001"
    ).fetchone()[0]
    assert gy is None


def test_locations_are_each_clinicians_georgia_addresses_once(built):
    con, out = built
    locations = out.replace("dac_ga.parquet", "dac_locations_ga.parquet")
    # NPI ...01 is listed at three addresses, under two groups.
    rows = con.execute(f"""
        SELECT address_line1, address_line2, city, state, postal_code, phone
        FROM read_parquet('{locations}') WHERE npi = 1000000001 ORDER BY 1
    """).fetchall()
    assert rows == [
        ("1 Peachtree St", "STE 300", "Atlanta", "GA", "30303", "4045550111"),
        ("200 Main St", "", "Decatur", "GA", "30030", None),
        ("55 10th St", "", "Atlanta", "GA", "30309", None),
    ]
    # One row per clinician and address, and none outside Georgia.
    assert con.execute(f"""
        SELECT count(*) FROM (SELECT npi, address_line1, address_line2, city, postal_code
                              FROM read_parquet('{locations}') GROUP BY ALL HAVING count(*) > 1)
    """).fetchone()[0] == 0
    assert con.execute(
        f"SELECT count(*) FROM read_parquet('{locations}') WHERE npi IN (2000000001, 2000000002)"
    ).fetchone()[0] == 0


@pytest.fixture(scope="module")
def built_roster_scoped(tmp_path_factory):
    """Exercise the parsed-rosters geo branch, the production path, with two
    parsed files, as two runs of parse may leave them. The National file's
    all-varchar NPI against the rosters' BIGINT once raised a real-data-only
    BinderException that the State='GA' path never hit."""
    data_dir = tmp_path_factory.mktemp("data")
    rosters = data_dir / "raw" / "anthem" / "providers"
    rosters.mkdir(parents=True)
    con = duckdb.connect()
    # Two GA fixture NPIs in one file; one more, and one out of state but
    # contracted, in the other. Every one must be kept: the scope is the parsed
    # rosters, not the State column, and not only the file parsed last.
    for name, npis in [("GA_A", [1000000001, 1000000002]), ("GA_B", [1000000003, 2000000001])]:
        values = ", ".join(f"(1, {npi}, '1000000010')" for npi in npis)
        con.execute(f"""
            COPY (SELECT * FROM (VALUES {values}) AS t(provider_group_id, npi, tin_value))
            TO '{rosters / name}.parquet' (FORMAT parquet)
        """)
    stdout, out = _build(data_dir)
    assert "NPIs on the rosters in" in stdout, f"expected the parsed-rosters geo branch:\n{stdout}"
    return con, out


def test_geo_scope_is_every_parsed_roster_not_state(built_roster_scoped):
    con, out = built_roster_scoped
    npis = {r[0] for r in con.execute(f"SELECT npi FROM read_parquet('{out}')").fetchall()}
    assert npis == {1000000001, 1000000002, 1000000003, 2000000001}
    # the out-of-state NPI is kept because a parsed roster lists it, and a GA
    # clinician on no roster is dropped
    assert 2000000001 in npis and 1000000004 not in npis
