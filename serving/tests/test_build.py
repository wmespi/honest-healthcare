"""Hermetic test for the build step (build/build.py).

No network, no DB — writes a handful of synthetic raw-Parquet files to a tmp
dir, runs build() against them with an injected plan-count map, and asserts the
product rules: `scope`, `is_sentinel`, and rule 5 (exact-duplicate lines
collapse, plan-specific wins; genuinely different rates are both kept).
Picked up by `make check-local`.
"""
import os

import duckdb
import pytest

from build.build import build

SLUG = "test-net"
NET = "Test Net"


def _raw(root):
    """Two files for one network. File 10 is plan-specific, file 20 shared.
    Both carry the identical line for group 1 / code 99213 (a rule-5 exact dup);
    file 20 also carries a *different* rate for that same line (kept) and a
    sub-$1 sentinel line for code 88888."""
    con = duckdb.connect()
    os.makedirs(f"{root}/anthem/prices/net={SLUG}", exist_ok=True)
    os.makedirs(f"{root}/anthem/group_sets", exist_ok=True)
    os.makedirs(f"{root}/anthem/providers", exist_ok=True)
    os.makedirs(f"{root}/anthem/codes", exist_ok=True)
    os.makedirs(f"{root}/nppes", exist_ok=True)

    cols = ("file_id, group_set_id, network_name, billing_code_type, billing_code, "
            "negotiation_arrangement, negotiated_type, negotiated_rate, expiration_date, "
            "service_code, billing_class, modifier, setting, net")

    def price(fid, code, rate):
        return (f"({fid}, 1, '{NET}', 'CPT', '{code}', 'ffs', 'negotiated', {rate}, "
                f"'2025-12-31', '11', 'professional', '', 'outpatient', '{SLUG}')")

    for fid in (10, 20):
        rows = [price(fid, "99213", 100.0)]
        if fid == 20:
            rows += [price(20, "99213", 60.0), price(20, "88888", 0.4)]
        con.execute(f"COPY (SELECT * FROM (VALUES {', '.join(rows)}) t({cols})) "
                    f"TO '{root}/anthem/prices/net={SLUG}/{fid}.parquet' (FORMAT parquet)")
        con.execute(f"COPY (SELECT * FROM (VALUES ({fid}, 1::BIGINT, 1::BIGINT)) "
                    f"t(file_id, group_set_id, provider_group_id)) "
                    f"TO '{root}/anthem/group_sets/{fid}.parquet' (FORMAT parquet)")
        con.execute(f"COPY (SELECT * FROM (VALUES ({fid}, 1::BIGINT, '{NET}', 111::BIGINT, "
                    f"'npi', '999')) t(file_id, provider_group_id, network_name, npi, "
                    f"tin_type, tin_value)) TO '{root}/anthem/providers/{fid}.parquet' (FORMAT parquet)")
    con.execute(f"COPY (SELECT * FROM (VALUES ('CPT', '99213', 'Office visit', 'x'), "
                f"('CPT', '88888', 'Panel', 'y')) t(billing_code_type, billing_code, name, description)) "
                f"TO '{root}/anthem/codes/c.parquet' (FORMAT parquet)")
    con.execute(f"COPY (SELECT * FROM (VALUES (111::BIGINT, 'individual', NULL::VARCHAR, 'Doe', 'Jane', "
                f"'207Q00000X', 'Family', false, false, '1 St', NULL::VARCHAR, 'Atlanta', 'GA', '30301')) "
                f"t(npi, entity_type, org_name, last_name, first_name, taxonomy_code, taxonomy_group, "
                f"is_hospital, is_clinic, address_line1, address_line2, city, state, postal_code)) "
                f"TO '{root}/nppes/ga_providers.parquet' (FORMAT parquet)")


@pytest.fixture()
def out(tmp_path, monkeypatch):
    for v in ("ANTHEM_DIR", "NPPES_DIR", "REFERENCE_DIR", "CMS_DIR", "SERVING_DIR",
              "DATABASE_URL", "TEST_DATABASE_URL", "DUCKDB_TMP"):
        monkeypatch.delenv(v, raising=False)
    root = str(tmp_path)
    _raw(root)
    serving = f"{root}/serving"
    build(root, serving, networks=[SLUG], test=False, plan_counts={10: 1, 20: 400})
    return duckdb.connect(), serving


def _rates(con, serving):
    return con.execute(
        f"SELECT billing_code, negotiated_rate, scope, is_sentinel, source_kind "
        f"FROM read_parquet('{serving}/rates/**/*.parquet') ORDER BY billing_code, negotiated_rate"
    ).fetchall()


def test_rule5_collapses_exact_dupes_plan_specific_wins(out):
    con, serving = out
    rows = _rates(con, serving)
    # 99213: the $100 line exists in both files -> one row, from the plan-specific
    # file (10); the $60 line is only in file 20 -> kept. 88888: one sentinel row.
    r99213 = [r for r in rows if r[0] == "99213"]
    assert [(r[1], r[4]) for r in r99213] == [(60.0, "shared"), (100.0, "plan_specific")]


def test_scope_and_sentinel(out):
    con, serving = out
    rows = _rates(con, serving)
    assert all(r[2] == "outpatient_prof" for r in rows)
    s = {r[0]: r[3] for r in rows}
    assert s["88888"] is True and s["99213"] is False


def test_dims_and_rollup_written(out):
    con, serving = out
    for f in ("group_members.parquet", "provider_dim.parquet", "code_dim.parquet",
              "evidence.parquet", "cross_network_rollup.parquet"):
        assert os.path.exists(f"{serving}/{f}")
    # the PCP taxonomy on the one provider flows through to service_lines
    sl = con.execute(f"SELECT service_lines FROM read_parquet('{serving}/provider_dim.parquet')").fetchone()
    assert sl[0] == "pcp"
