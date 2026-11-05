"""Hermetic test for the exchange plan builder (reference/exchange_plans.py): runs it on
real rows from Georgia's 2026 public use files in test isolation (writes
data-test/reference/, never data/)."""
import os
import subprocess
import sys

import duckdb
import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLES = os.path.join(REPO, "reference/testdata")
OUT = os.path.join(REPO, "data-test/reference")
TABLES = ("plans", "networks", "premiums", "cost_sharing")


def build(*extra, plan=f"{SAMPLES}/exchange_plan_sample.csv"):
    files = ["--plan-file", plan,
             "--network-file", f"{SAMPLES}/exchange_network_sample.csv",
             "--rate-file", f"{SAMPLES}/exchange_rate_sample.csv",
             "--benefits-file", f"{SAMPLES}/exchange_benefits_sample.csv"]
    return subprocess.run([sys.executable, "-m", "reference.exchange_plans", *files, "--test", *extra],
                          cwd=REPO, capture_output=True, text=True)


def read(table):
    return duckdb.connect().execute(f"SELECT * FROM read_parquet('{OUT}/exchange_{table}_ga.parquet')").fetchall()


@pytest.fixture(scope="module")
def built():
    r = build("--year", "2026")
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    yield {t: read(t) for t in TABLES}
    for t in TABLES:
        try:
            os.remove(f"{OUT}/exchange_{t}_ga.parquet")
        except FileNotFoundError:
            pass


def test_a_plan_is_one_row_named_after_its_standard_version(built):
    plans = {p[0]: p for p in built["plans"]}
    assert plans["45334GA0020002"] == (
        "45334GA0020002", 2026, "45334", "Anthem Blue Cross and Blue Shield",
        "Anthem Silver Blue Value HMO 4000 $10 $0 Virtual PCP $0 Select Drugs",
        "Silver", "HMO", False, "45334-GAN001", 4000.0)


def test_cost_sharing_is_the_standard_versions_in_network_wording_by_benefit(built):
    silver = {r[1]: r[2:] for r in built["cost_sharing"] if r[0] == "45334GA0020002"}
    assert silver == {
        "Imaging (CT/PET Scans, MRIs)": (True, "$300.00 Copay after deductible",
                                         "50.00% Coinsurance after deductible"),
        "X-rays and Diagnostic Imaging": (True, "Not Applicable", "40.00% Coinsurance after deductible"),
        "Preventive Care/Screening/Immunization": (True, "No Charge", "Not Applicable"),
        # Georgia's file starts this name with a tab.
        "Outpatient Facility Fee (e.g., Ambulatory Surgery Center)": (True, "Not Applicable",
                                                                      "40.00% Coinsurance after deductible"),
        "Acupuncture": (False, None, None)}
    assert {r[0] for r in built["cost_sharing"]} == {"45334GA0020002", "45334GA0020008", "89942GA0050030"}


def test_dental_plans_are_dropped(built):
    assert {p[0] for p in built["plans"]} == {"45334GA0020002", "45334GA0020008", "89942GA0050030"}
    assert all(p[0] != "13535GA0140001" for p in built["premiums"])


def test_referral_rule_and_network_come_from_the_plan(built):
    kaiser = next(p for p in built["plans"] if p[0] == "89942GA0050030")
    assert kaiser[7] is True and kaiser[8] == "89942-GAN003"
    assert sorted(built["networks"]) == [
        ("45334-GAN001", "45334", "Blue Value Individual Network HMO and Rx Choice Tiered Network"),
        ("89942-GAN003", "89942", "Kaiser Permanente HMO - Individual")]


def test_every_age_gets_a_premium(built):
    area1 = {age: (rate, tobacco) for plan, area, age, rate, tobacco in built["premiums"]
             if plan == "45334GA0020002" and area == 1}
    assert all(area1[age] == (404.44, 404.44) for age in range(15))
    assert area1[40] == (675.65, 743.22)
    assert area1[64] == (1586.04, 1744.65)
    assert ("45334GA0020002", 11, 40, 604.89, 665.38) in built["premiums"]


def test_files_from_another_year_are_refused():
    r = build("--year", "2027")
    assert r.returncode != 0 and "expected 2027" in r.stdout + r.stderr


def test_a_plan_listed_twice_is_refused(tmp_path):
    lines = open(f"{SAMPLES}/exchange_plan_sample.csv").read().splitlines()
    twice = tmp_path / "plan.csv"
    silver = next(line for line in lines if '"45334GA0020002-01"' in line)
    twice.write_text("\n".join(lines + [silver]) + "\n")
    r = build("--year", "2026", plan=str(twice))
    assert r.returncode != 0 and "plan_id isn't unique" in r.stdout + r.stderr
