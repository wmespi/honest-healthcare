"""Hermetic test for the Medicaid spending builder (ingestion/reference/medicaid_spending.py):
a tiny Parquet and a tiny NPPES Georgia subset in tmp, no network."""
import duckdb
import pytest

from ingestion.reference.medicaid_spending import build

# (billing NPI, servicing NPI, code, month, claim lines)
ROWS = [
    ("100", "1", "99213", "2025-12", 5),    # servicing NPI 1 is in Georgia
    ("100", "1", "99213", "2025-01", 7),    # the window's first month
    ("100", "1", "99213", "2024-12", 100),  # before the window
    ("2", "", "99213", "2025-06", 20),      # servicing blank: the billing NPI, 2, is in Georgia
    ("2", "3", "99214", "2025-06", 30),     # servicing 3 is not in Georgia, though billing 2 is
    ("4", "9", "99213", "2025-06", 40),     # a location-only Georgia NPI (9)
    ("5", "abc", "99213", "2025-06", 50),   # a servicing NPI that isn't numeric
    ("x", "", "99213", "2025-06", 60),      # a billing NPI that isn't numeric
    ("100", "1", "99213", "2025-12", 3),    # more lines on the same npi and code
]


@pytest.fixture
def built(tmp_path):
    con = duckdb.connect()
    con.execute("CREATE TABLE t(BILLING_PROVIDER_NPI_NUM VARCHAR, SERVICING_PROVIDER_NPI_NUM VARCHAR, "
                "HCPCS_CODE VARCHAR, CLAIM_FROM_MONTH VARCHAR, TOTAL_PATIENTS BIGINT, "
                "TOTAL_CLAIM_LINES BIGINT, TOTAL_PAID DOUBLE)")
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, 12, ?, 1.0)", [(b, s, c, m, n) for b, s, c, m, n in ROWS])
    src, providers, locations, out = (str(tmp_path / n) for n in ("src.parquet", "p.parquet", "l.parquet", "o.parquet"))
    con.execute(f"COPY t TO '{src}' (FORMAT parquet)")
    con.execute(f"COPY (SELECT * FROM (VALUES (1::BIGINT), (2::BIGINT), (5::BIGINT)) v(npi)) TO '{providers}' (FORMAT parquet)")
    con.execute(f"COPY (SELECT * FROM (VALUES (9::BIGINT)) v(npi)) TO '{locations}' (FORMAT parquet)")
    through = build(con, src, [providers, locations], out)
    rows = con.execute(f"SELECT npi, hcpcs_cd, claim_lines, through FROM read_parquet('{out}') ORDER BY ALL").fetchall()
    return through, rows


def test_the_window_is_the_latest_twelve_months(built):
    through, rows = built
    assert through == "2025-12"
    assert (1, "99213", 15, "2025-12") in rows  # 5 + 7 + 3, not the 2024 month


def test_the_servicing_npi_counts_else_the_billing_npi(built):
    _, rows = built
    assert (2, "99213", 20, "2025-12") in rows  # servicing blank
    assert not any(r[1] == "99214" for r in rows)  # servicing 3 is outside Georgia


def test_scope_is_the_georgia_npis_and_drops_non_numeric_ones(built):
    _, rows = built
    assert [r[0] for r in rows] == [1, 2, 9]  # 5's servicing NPI isn't numeric, "x" isn't either
