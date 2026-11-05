"""Hermetic tests for the MIPS and ZIP-centroid reference builders (no network)."""
import os
import subprocess
import sys

import duckdb

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(module, data_dir, *args):
    r = subprocess.run([sys.executable, "-m", module, "--data-dir", str(data_dir), *args],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_mips_prefers_individual_and_scopes_to_ga(tmp_path):
    os.makedirs(tmp_path / "nppes")
    duckdb.connect().execute(
        f"COPY (SELECT * FROM (VALUES (1), (2), (3)) t(npi)) TO '{tmp_path}/nppes/ga_providers.parquet'")
    csv = tmp_path / "ec.csv"
    csv.write_text(
        "NPI, Org_PAC_ID, Provider Last Name, Provider First Name, source, "
        "Facility-based scoring Certification number, Facility Name, Quality_category_score, "
        "PI_category_score, IA_category_score, Cost_category_score, final_MIPS_score_without_CPB, "
        "final_MIPS_score\n"
        "1,,A,B,group,,,,,,,50,50\n"
        "1,,A,B,individual,,,,,,,80,80\n"
        "2,,A,B,apm,,,,,,,0,0\n"
        "3,,A,B,individual,,,,,,,,\n"
        "99,,A,B,individual,,,,,,,70,70\n")
    _run("reference.mips", tmp_path, "--file", str(csv), "--year", "2024")
    rows = duckdb.connect().execute(
        f"SELECT npi, mips_score, mips_source, performance_year "
        f"FROM read_parquet('{tmp_path}/reference/mips_ga.parquet') ORDER BY npi").fetchall()
    # npi 1: individual beats group; npi 2: a published 0 is kept; 3: no score, 99: not GA
    assert rows == [(1, 80.0, "individual", 2024), (2, 0.0, "apm", 2024)]


def test_zip_centroids(tmp_path):
    txt = tmp_path / "gaz.txt"
    txt.write_text("GEOID\tALAND\tINTPTLAT\tINTPTLONG\n30309\t1\t33.799\t-84.388\n501\t1\t40.8\t-73.0\n")
    _run("reference.zip_centroids", tmp_path, "--file", str(txt))
    rows = duckdb.connect().execute(
        f"SELECT * FROM read_parquet('{tmp_path}/reference/zip_centroids.parquet') ORDER BY zip").fetchall()
    assert rows == [("00501", 40.8, -73.0), ("30309", 33.799, -84.388)]
