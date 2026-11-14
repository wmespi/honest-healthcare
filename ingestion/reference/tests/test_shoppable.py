"""Hermetic test for the shoppable-items builder (ingestion/reference/shoppable.py): runs
the build on small inline CSVs, in a temp directory."""
import duckdb
import pytest

from ingestion.reference import shoppable

HEADER = ("HCPCS_Cd,RBCS_Id,RBCS_Cat,RBCS_Cat_Desc,RBCS_Cat_Subcat,RBCS_Subcat_Desc,RBCS_FamNumb,"
          "RBCS_Family_Desc,RBCS_Major_Ind,HCPCS_Cd_Add_Dt,HCPCS_Cd_End_Dt,RBCS_Latest_Assignment\n")
RBCS = HEADER + "\n".join([
    "00100,AA000N,A,Anesthesia,AA,Anesthesia,000,No RBCS Family,N,01/01/1989,12/31/9999,1",
    "0001A,RI035N,R,Treatment,RI,Injections,035,Old family,N,12/11/2020,10/31/2023,0",
    "0001A,RI039N,R,Treatment,RI,Injections,039,Vaccine Administration,N,12/11/2020,10/31/2023,1",
    "70551,II001N,I,Imaging,II,Standard Imaging,001,MRI Brain,N,01/01/2000,12/31/2010,1",
    "70551,II002N,I,Imaging,II,Standard Imaging,002,MRI Brain Newer,N,01/01/2000,12/31/9999,1",
]) + "\n"
ITEMS = "billing_code_type,billing_code\nMS-DRG,470\nCPT,70551\nCPT,00100\nHCPCS,0001A\n"


def _run(tmp_path, items=ITEMS, rbcs=RBCS):
    (tmp_path / "items.csv").write_text(items)
    (tmp_path / "rbcs.csv").write_text(rbcs)
    out = str(tmp_path / "out.parquet")
    con = duckdb.connect()
    n = shoppable.build(con, str(tmp_path / "items.csv"), str(tmp_path / "rbcs.csv"), out)
    return n, con.execute(f"SELECT * FROM read_parquet('{out}')").fetchall()


def test_rows_are_sorted_and_classified(tmp_path):
    n, rows = _run(tmp_path)
    assert n == 4
    assert rows == [
        ("00100", "CPT", "Anesthesia", "Anesthesia", "No RBCS Family"),
        ("70551", "CPT", "Imaging", "Standard Imaging", "MRI Brain Newer"),
        ("0001A", "HCPCS", "Treatment", "Injections", "Vaccine Administration"),
        ("470", "MS-DRG", None, None, None),
    ]


def test_missing_column_stops(tmp_path):
    with pytest.raises(SystemExit, match="missing column"):
        _run(tmp_path, rbcs=RBCS.replace("RBCS_Family_Desc", "Other"))


def test_repeated_code_stops(tmp_path):
    with pytest.raises(SystemExit, match="repeats"):
        _run(tmp_path, items=ITEMS + "CPT,70551\n")


def test_low_match_stops(tmp_path):
    with pytest.raises(SystemExit, match="RBCS row"):
        _run(tmp_path, items="billing_code_type,billing_code\nCPT,99999\n")
