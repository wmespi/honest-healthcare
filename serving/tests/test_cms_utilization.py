"""Hermetic test for the CMS utilization builder (reference/cms_utilization.py).

No network, no live API — runs the builder against a committed 15-row CSV
fixture in test isolation (writes data-test/cms/, never data/cms/).
"""
import os
import subprocess
import sys

import duckdb
import pytest

# Repo-root relative so this runs on the host (`make check-local`) too; in the
# container REPO resolves to /app. GH #59.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURE = os.path.join(REPO, "reference/testdata/cms_sample.csv")
OUT = os.path.join(REPO, "data-test/cms/ga_provider_service.parquet")


@pytest.fixture(scope="module")
def built():
    r = subprocess.run(
        [sys.executable, "-m", "reference.cms_utilization",
         "--cms-file", FIXTURE, "--year", "2024", "--test"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    yield duckdb.connect()
    # Leave data-test/ clean — a stale ga_providers-adjacent parquet here would
    # otherwise perturb a later `make test-e2e` in the same environment.
    try:
        os.remove(OUT)
    except FileNotFoundError:
        pass


def test_only_georgia_rows_kept(built):
    # fixture: 15 rows = 12 GA + 1 FL + 1 TX + 1 corrupt-NPI (GA but unparseable)
    n = built.execute(f"SELECT count(*) FROM read_parquet('{OUT}')").fetchone()[0]
    assert n == 12


def test_corrupt_npi_dropped(built):
    bad = built.execute(
        f"SELECT count(*) FROM read_parquet('{OUT}') WHERE npi IS NULL"
    ).fetchone()[0]
    assert bad == 0


def test_schema_and_types(built):
    cols = {r[0]: r[1] for r in built.execute(f"DESCRIBE SELECT * FROM read_parquet('{OUT}')").fetchall()}
    assert set(cols) == {
        "npi", "hcpcs_cd", "place_of_service", "tot_benes", "tot_srvcs",
        "tot_bene_day_srvcs", "avg_mdcr_alowd_amt", "provider_type",
        "hcpcs_drug_ind", "year",
    }
    assert cols["npi"] == "BIGINT"
    assert cols["tot_benes"] == "INTEGER"
    assert cols["avg_mdcr_alowd_amt"] == "DOUBLE"


def test_known_pair_present_with_volume(built):
    # the LCSW billing psychotherapy — the case the plausibility heuristic
    # would have flagged "unlikely"
    row = built.execute(f"""
        SELECT tot_benes, tot_srvcs, ROUND(avg_mdcr_alowd_amt, 2), provider_type
        FROM read_parquet('{OUT}') WHERE npi = 3333333333 AND hcpcs_cd = '90837'
    """).fetchone()
    assert row == (60, 410.0, 132.10, "Clinical Social Worker")


def test_facility_office_split_preserved(built):
    # NPI 2222222222 bills 93000 in O and 93306 in F — both rows, distinct POS
    pos = built.execute(f"""
        SELECT hcpcs_cd, place_of_service FROM read_parquet('{OUT}')
        WHERE npi = 2222222222 ORDER BY hcpcs_cd
    """).fetchall()
    assert pos == [("93000", "O"), ("93306", "F")]


def test_drug_indicator_kept(built):
    drug = built.execute(f"""
        SELECT hcpcs_drug_ind FROM read_parquet('{OUT}')
        WHERE npi = 6666666666 AND hcpcs_cd = 'J9299'
    """).fetchone()[0]
    assert drug == "Y"
