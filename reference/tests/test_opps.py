"""Hermetic test for the OPPS builder (reference/opps.py): runs it on a committed
Addendum B fixture in test isolation (writes data-test/reference/, never data/)."""
import os
import subprocess
import sys

import duckdb
import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLE = os.path.join(REPO, "reference/testdata/opps_sample.txt")
OUT = os.path.join(REPO, "data-test/reference/opps.parquet")


@pytest.fixture(scope="module")
def rows():
    r = subprocess.run([sys.executable, "-m", "reference.opps", "--addendum-file", SAMPLE, "--test"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    got = duckdb.connect().execute(
        f"SELECT billing_code, billing_code_type, status_indicator, apc, payment_rate, year "
        f"FROM read_parquet('{OUT}')").fetchall()
    yield {r[0]: r for r in got}
    try:
        os.remove(OUT)
    except FileNotFoundError:
        pass


def test_priced_codes_carry_the_national_rate(rows):
    assert rows["70551"] == ("70551", "CPT", "Q3", "5523", 243.77, 2026)
    assert rows["72148"][4] == 243.77


def test_thousands_separator_is_parsed(rows):
    assert rows["78452"][4] == 1322.69


def test_codes_without_an_opps_rate_are_dropped(rows):
    assert "77067" not in rows and "0001F" not in rows
