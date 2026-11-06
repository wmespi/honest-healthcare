"""Hermetic test for the hospital builder (ingestion/reference/hospitals.py): runs it on real rows
from CMS's Hospital General Information in test isolation (writes data-test/ingestion/reference/,
never data/)."""
import os
import subprocess
import sys

import duckdb
import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SAMPLE = os.path.join(REPO, "ingestion/reference/testdata/hospitals_sample.csv")
OUT = os.path.join(REPO, "data-test/ingestion/reference/hospitals_ga.parquet")


@pytest.fixture(scope="module")
def rows():
    r = subprocess.run([sys.executable, "-m", "ingestion.reference.hospitals", "--file", SAMPLE, "--test"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    got = duckdb.connect().execute(f"SELECT * FROM read_parquet('{OUT}')").fetchall()
    yield {row[0]: row for row in got}
    try:
        os.remove(OUT)
    except FileNotFoundError:
        pass


def test_a_hospital_is_one_row_keyed_by_ccn(rows):
    assert rows["110001"] == (
        "110001", "HAMILTON MEDICAL CENTER", "1200 MEMORIAL DRIVE", "DALTON", "GA", "30720",
        "WHITFIELD", "(706) 272-6105", "Acute Care Hospitals", "Voluntary non-profit - Private",
        False, 3)


def test_only_georgia_is_kept(rows):
    assert set(rows) == {"110001", "110002", "110071"}


def test_an_unrated_hospital_has_no_stars(rows):
    assert rows["110071"][-1] is None and rows["110002"][-1] == 1
