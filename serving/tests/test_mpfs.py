"""Hermetic test for the MPFS builder (reference/mpfs.py).

No network — runs the builder against committed PPRRVU + GPCI CSV fixtures in
test isolation (writes data-test/reference/, never data/). Picked up by
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
RVU = os.path.join(REPO, "reference/testdata/mpfs_sample.csv")
GPCI = os.path.join(REPO, "reference/testdata/mpfs_gpci_sample.csv")
OUT = os.path.join(REPO, "data-test/reference/mpfs_ga.parquet")


@pytest.fixture(scope="module")
def built():
    r = subprocess.run(
        [sys.executable, "-m", "reference.mpfs",
         "--rvu-file", RVU, "--gpci-file", GPCI, "--year", "2024", "--test"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    yield duckdb.connect()
    try:
        os.remove(OUT)
    except FileNotFoundError:
        pass


def q(built, sql):
    return built.execute(sql.replace("OUT", f"read_parquet('{OUT}')")).fetchall()


def test_schema(built):
    cols = {r[0]: r[1] for r in built.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{OUT}')").fetchall()}
    assert set(cols) == {
        "billing_code", "billing_code_type", "modifier", "pos", "locality",
        "medicare_allowed", "status",
    }
    assert cols["medicare_allowed"] == "DOUBLE"


def test_rvu_formula_nonfacility(built):
    # 99213 loc 01: (1.00*1.0 + 1.00*1.0 + 0.10*1.0) * 32.0 = 67.20
    # 99213 loc 99: (1.00*1.0 + 1.00*0.9 + 0.10*0.8) * 32.0 = 63.36
    rows = dict(q(built, "SELECT locality, medicare_allowed FROM OUT "
                         "WHERE billing_code='99213' AND modifier='' AND pos='nonfacility'"))
    assert rows == {"01": 67.20, "99": 63.36}


def test_rvu_formula_facility_uses_facility_pe(built):
    # 99213 loc 01 facility: (1.00 + 0.50*1.0 + 0.10*1.0) * 32.0 = 51.20
    row = q(built, "SELECT medicare_allowed FROM OUT WHERE billing_code='99213' "
                   "AND modifier='' AND pos='facility' AND locality='01'")
    assert row == [(51.20,)]


def test_component_split_26_tc(built):
    mods = {r[0] for r in q(built, "SELECT DISTINCT modifier FROM OUT WHERE billing_code='70450'")}
    assert {"", "26", "TC"} <= mods
    # TC has FACILITY NA → only the non-facility row exists
    tc_pos = {r[0] for r in q(built, "SELECT DISTINCT pos FROM OUT "
                                     "WHERE billing_code='70450' AND modifier='TC'")}
    assert tc_pos == {"nonfacility"}
    # TC loc 01: (0 + 3.60*1.0 + 0.10*1.0) * 32.0 = 118.40
    assert q(built, "SELECT medicare_allowed FROM OUT WHERE billing_code='70450' "
                    "AND modifier='TC' AND locality='01'") == [(118.40,)]


def test_bundled_status_dropped(built):
    # 36415 is STATUS CODE 'B' (bundled) — no row at all
    assert q(built, "SELECT count(*) FROM OUT WHERE billing_code='36415'") == [(0,)]


def test_carrier_priced_kept_with_null_amount(built):
    rows = q(built, "SELECT DISTINCT status, medicare_allowed FROM OUT WHERE billing_code='J9299'")
    assert rows == [("C", None)]


def test_facility_na_indicator_nulls_facility_row(built):
    # 93000 has FACILITY NA → no facility row, non-facility present (status R)
    assert q(built, "SELECT DISTINCT pos FROM OUT WHERE billing_code='93000'") == [("nonfacility",)]
    assert q(built, "SELECT DISTINCT status FROM OUT WHERE billing_code='93000'") == [("R",)]


def test_billing_code_type_split(built):
    types = dict(q(built, "SELECT DISTINCT billing_code, billing_code_type FROM OUT "
                          "WHERE billing_code IN ('99213','J9299')"))
    assert types == {"99213": "CPT", "J9299": "HCPCS"}


def test_only_georgia_localities(built):
    locs = {r[0] for r in q(built, "SELECT DISTINCT locality FROM OUT")}
    assert locs == {"01", "99"}  # Florida row in the GPCI fixture is filtered out
