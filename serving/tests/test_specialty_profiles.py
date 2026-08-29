"""Hermetic test for reference/specialty_profiles.py.

Writes tiny CMS / NPPES / NUCC parquets under data-test/, runs the builder, and
checks the prevalence math + the min-provider guard. No network, no live API.
"""
import subprocess
import sys

import duckdb
import pytest

REPO = "/app"
CMS = "/app/data-test/cms/ga_provider_service.parquet"
NPPES = "/app/data-test/nppes/ga_providers.parquet"
NUCC = "/app/data-test/reference/nucc_taxonomy.parquet"
OUT = "/app/data-test/reference/specialty_procedure_profiles.parquet"


@pytest.fixture(scope="module")
def built():
    import os
    for p in (CMS, NPPES, NUCC):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    c = duckdb.connect()

    # 25 "Cardiology" NPIs: 20 bill 93000, 1 also bills 45378.
    #  4 "Chiropractor" NPIs (below the 20-provider guard) — must be dropped.
    card = [1000 + i for i in range(25)]
    chiro = [2000 + i for i in range(4)]
    nppes_vals = ",".join(f"({n},'CARD')" for n in card) + "," + \
                 ",".join(f"({n},'CHIR')" for n in chiro)
    c.execute(f"COPY (SELECT * FROM (VALUES {nppes_vals}) t(npi, taxonomy_code)) "
              f"TO '{NPPES}' (FORMAT parquet)")
    c.execute(f"COPY (SELECT * FROM (VALUES ('CARD','Cardiology'),('CHIR','Chiropractor')) "
              f"t(taxonomy_code, classification)) TO '{NUCC}' (FORMAT parquet)")

    # all 25 Cardiology NPIs have >=1 claim (the prevalence denominator is
    # "specialty providers with any Medicare claim"): 20 bill 93000, the other
    # 5 bill only 99213, and NPI card[0] also bills 45378.
    cms_rows = [(n, "93000") for n in card[:20]] + \
               [(n, "99213") for n in card[20:]] + \
               [(card[0], "45378")] + \
               [(n, "98940") for n in chiro]
    cms_vals = ",".join(f"({n},'{code}')" for n, code in cms_rows)
    c.execute(f"COPY (SELECT * FROM (VALUES {cms_vals}) t(npi, hcpcs_cd)) "
              f"TO '{CMS}' (FORMAT parquet)")

    r = subprocess.run(
        [sys.executable, "-m", "reference.specialty_profiles", "--test"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"builder failed:\n{r.stdout}\n{r.stderr}"
    return duckdb.connect()


def test_prevalence_math(built):
    # 20 of 25 Cardiology NPIs billed 93000 → 0.8
    row = built.execute(
        f"SELECT billers, specialty_providers, prevalence FROM read_parquet('{OUT}') "
        f"WHERE specialty = 'Cardiology' AND hcpcs_cd = '93000'"
    ).fetchone()
    assert row == (20, 25, 0.8)


def test_low_prevalence_code_dropped_at_floor(built):
    # 1 of 25 billed 45378 → 0.04, above the 0.005 floor, so it's kept
    n = built.execute(
        f"SELECT count(*) FROM read_parquet('{OUT}') "
        f"WHERE specialty = 'Cardiology' AND hcpcs_cd = '45378'"
    ).fetchone()[0]
    assert n == 1


def test_small_specialty_dropped(built):
    # Chiropractor has only 4 providers (< MIN_SPECIALTY_PROVIDERS=20) → no rules
    n = built.execute(
        f"SELECT count(*) FROM read_parquet('{OUT}') WHERE specialty = 'Chiropractor'"
    ).fetchone()[0]
    assert n == 0
