"""Hermetic tests for the CMS quality-measure builders (hospital_measures, asc_measures,
clinician_volume): each runs `build` on tiny CSVs written here, never the network."""
import datetime

import duckdb
import pytest

from ingestion.reference import asc_measures, clinician_volume, hospital_measures as hm


def _write(path, text):
    path.write_text(text.strip() + "\n")
    return str(path)


@pytest.fixture
def hospital_files(tmp_path):
    head = '"Facility ID","State",'
    return {
        hm.UNPLANNED: _write(tmp_path / "u.csv", head + '"Measure ID","Compared to National","Denominator","Score","Start Date","End Date"\n'
            "110001,GA,OP_32,No Different Than the National Rate,100,11,01/01/2022,12/31/2024\n"
            "110001,GA,OP_36,Better than expected,50,0.7,01/01/2024,12/31/2024\n"
            "110002,GA,OP_32,Not Available,Not Available,Not Available,01/01/2022,12/31/2024\n"
            "110002,GA,OP_36,Not Available,Not Available,Not Available,01/01/2024,12/31/2024\n"
            "110002,GA,OP_35_ADM,Not Available,Not Available,Not Available,01/01/2024,12/31/2024\n"
            "010001,AL,OP_32,No Different Than the National Rate,100,11,01/01/2022,12/31/2024"),
        hm.TIMELY: _write(tmp_path / "t.csv", head + '"Measure ID","Score","Sample","Start Date","End Date"\n'
            "110001,GA,OP_29,100,107,01/01/2024,12/31/2024\n110001,GA,OP_31,Not Available,Not Available,01/01/2024,12/31/2024\n"
            "110001,GA,OP_18a,5,9,01/01/2024,12/31/2024\n010001,AL,OP_29,1,1,01/01/2024,12/31/2024"),
        hm.IMAGING: _write(tmp_path / "i.csv", head + '"Measure ID","Score","Start Date","End Date"\n'
            "110001,GA,OP-10,6.4,07/01/2024,06/30/2025\n110001,GA,OP-39,Not Available,07/01/2024,06/30/2025"),
        hm.HCAHPS: _write(tmp_path / "h.csv", head + '"HCAHPS Measure ID","Patient Survey Star Rating","Number of Completed Surveys","Start Date","End Date"\n'
            "110001,GA,H_STAR_RATING,4,1977,01/01/2025,12/31/2025\n110001,GA,H_COMP_1_A_P,Not Applicable,1977,01/01/2025,12/31/2025"),
        hm.TIMELY_NAT: _write(tmp_path / "tn.csv", '"Measure ID","Score","Start Date","End Date"\n'
            "OP_29,93,01/01/2024,12/31/2024\nOP_31,97,01/01/2024,12/31/2024\nOP_29_NINETIETH,100,01/01/2024,12/31/2024"),
        hm.IMAGING_NAT: _write(tmp_path / "in.csv", '"Measure ID","Score","Start Date","End Date"\n'
            "OP_10,5.8,07/01/2024,06/30/2025\nOP_39,9,07/01/2024,06/30/2025"),
    }


def test_hospital_measures_are_long_georgia_only_and_null_when_unavailable(hospital_files, tmp_path):
    con = duckdb.connect()
    out_ga, out_nat = hm.build(con, hospital_files, str(tmp_path))
    rows = {(r[0], r[1]): r for r in con.execute(f"SELECT * FROM '{out_ga}'").fetchall()}
    assert rows[("110001", "OP-32")] == ("110001", "OP-32", 11.0, "No Different Than the National Rate", 100,
                                         datetime.date(2022, 1, 1), datetime.date(2024, 12, 31))
    assert rows[("110002", "OP-32")][2:5] == (None, None, None)
    assert rows[("110001", "H_STAR_RATING")][2:5] == (4.0, None, 1977)
    assert rows[("110001", "OP-31")][2] is None
    assert {m for _, m in rows} == {"OP-32", "OP-36", "OP-29", "OP-31", "OP-10", "OP-39", "H_STAR_RATING"}
    assert all(c.startswith("11") for c, _ in rows)
    nat = dict(con.execute(f"SELECT measure, score FROM '{out_nat}'").fetchall())
    assert nat == {"OP-10": 5.8, "OP-39": 9.0, "OP-29": 93.0, "OP-31": 97.0}


def test_hospital_measures_stop_when_cms_drops_a_column(hospital_files, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text('"Facility ID","State","Measure ID","Score"\n110001,GA,OP-10,1\n')
    with pytest.raises(SystemExit, match="CMS changed the file"):
        hm.build(duckdb.connect(), {**hospital_files, hm.IMAGING: str(bad)}, str(tmp_path))


def test_asc_measures_are_long_with_national_values(tmp_path):
    cols = ["Facility ID", "NPI", "State", "Year", "ASC-9 Rate*", "ASC-11 Rate*"]
    for m, s in asc_measures.RISK_ADJUSTED.items():
        cols += [f"{m} Total Cases", f"{m} Performance Category", f"{m} {s}"]
    blank = ["N/A"] * 3
    row = lambda fid, npi, st, a9, a11, c12: ",".join(  # noqa: E731
        [fid, npi, st, "2025", a9, a11] + c12 + blank * 3)
    fac = _write(tmp_path / "f.csv", ",".join(cols) + "\n"
                 + row("11C1", "1003", "GA", "96.15", "N/A", ["1269", "No Different Than the National Rate", "11.9"]) + "\n"
                 + row("11C2", "1004", "GA", "N/A", "N/A", ["N/A", "N/A", "N/A"]) + "\n"
                 + row("05C1", "1005", "CA", "50", "N/A", ["N/A", "N/A", "N/A"]))
    ncols = ["Year"] + [c for pair in asc_measures.NATIONAL_COLS.values() for c in pair if c]
    nat = _write(tmp_path / "n.csv", ",".join(ncols) + "\n" + ",".join(["2025"] + [str(i) for i in range(len(ncols) - 1)]))
    con = duckdb.connect()
    out_ga, out_nat = asc_measures.build(con, fac, nat, str(tmp_path))
    rows = con.execute(f"SELECT npi, ccn, measure, score, compared, sample, period FROM '{out_ga}' ORDER BY measure").fetchall()
    assert rows == [(1003, "11C1", "ASC-12", 11.9, "No Different Than the National Rate", 1269, "2025"),
                    (1003, "11C1", "ASC-9", 96.15, None, None, "2025")]
    assert con.execute(f"SELECT count(*) FROM '{out_nat}'").fetchone()[0] == 6


def test_clinician_volume_keeps_georgia_npis_and_cms_text(tmp_path):
    src = _write(tmp_path / "u.csv", "NPI,Ind_PAC_ID,Procedure_Category,Count,Percentile,Profile_Display_Indicator\n"
                 "1001,1,Hip replacement,34,79,Y\n1001,1,Knee replacement,1-10,,N\n2002,2,Hip replacement,57,10,Y")
    nppes = str(tmp_path / "ga.parquet")
    duckdb.connect().execute(f"COPY (SELECT 1001::BIGINT AS npi) TO '{nppes}' (FORMAT parquet)")
    out = str(tmp_path / "out.parquet")
    con = duckdb.connect()
    clinician_volume.build(con, src, nppes, "2026-10-15", out)
    assert con.execute(f"SELECT * FROM '{out}' ORDER BY category").fetchall() == [
        (1001, "Hip replacement", "34", 79, True, datetime.date(2026, 10, 15)),
        (1001, "Knee replacement", "1-10", None, False, datetime.date(2026, 10, 15))]
    with pytest.raises(SystemExit, match="CMS changed the file"):
        (tmp_path / "bad.csv").write_text("NPI,Count\n1001,2\n")
        clinician_volume.build(con, str(tmp_path / "bad.csv"), nppes, None, out)
