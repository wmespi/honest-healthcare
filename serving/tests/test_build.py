"""Hermetic test for the build step (build/build.py).

No network, no DB — writes a handful of synthetic raw-Parquet files to a tmp
dir, runs build() against them with an injected plan-count map, and asserts the
product rules: `scope`, `is_sentinel`, `source_kind` (plan_specific when the
file serves one plan, else shared), that `rates` is the parser's price grain
(no fan-out), and that `rate_hist.n` is roster-weighted. Picked up by
`make check-local`.
"""
import json
import os

import duckdb
import pytest

from build.build import OUTPATIENT_PROF, TABLES, build
from serving.data_sources import outpatient_scope

SLUG = "test-net"
NET = "Test Net"
# Punctuation-only variant of NET — network_slug() collapses non-alnum runs to
# one hyphen, so this is a genuinely distinct Anthem network_name that
# slugifies to the SAME `net` partition key as NET (a real corpus fact, #100).
NET2 = "Test, Net"


def _raw(root, collision=False):
    """Two files for one network. File 10 serves 1 plan (-> plan_specific),
    file 20 serves 400 (-> shared). Both carry a $100 line for group 1 / code
    99213; file 20 also carries a $60 line for it and a sub-$1 sentinel line for
    88888. A second code 99215 gets many mid-priced rows so 99213's $100 stays
    above its own 5%-of-median sentinel ceiling.

    `collision=True` adds file 30, under NET2, in the SAME `net=` partition
    directory as NET — the two-real-networks-one-slug case."""
    con = duckdb.connect()
    for d in ("prices/net=" + SLUG, "group_sets", "providers", "codes"):
        os.makedirs(f"{root}/anthem/{d}", exist_ok=True)
    os.makedirs(f"{root}/nppes", exist_ok=True)

    cols = ("file_id, group_set_id, network_name, billing_code_type, billing_code, "
            "negotiation_arrangement, negotiated_type, negotiated_rate, expiration_date, "
            "service_code, billing_class, modifier, setting, net")

    def price(fid, code, rate):
        return (f"({fid}, 1, '{NET}', 'CPT', '{code}', 'ffs', 'negotiated', {rate}, "
                f"'2025-12-31', '11', 'professional', '', 'outpatient', '{SLUG}')")

    for fid in (10, 20):
        rows = [price(fid, "99213", 100.0)] + [price(fid, "99215", 90.0 + i) for i in range(20)]
        if fid == 20:
            rows += [price(20, "99213", 60.0), price(20, "88888", 0.4)]
        con.execute(f"COPY (SELECT * FROM (VALUES {', '.join(rows)}) t({cols})) "
                    f"TO '{root}/anthem/prices/net={SLUG}/{fid}.parquet' (FORMAT parquet)")
        # group_set 1 has two provider groups -> rate_hist.n weights each price
        # row by 2, while n_rates counts it once.
        con.execute(f"COPY (SELECT * FROM (VALUES ({fid}, 1::BIGINT, 1::BIGINT), "
                    f"({fid}, 1::BIGINT, 2::BIGINT)) "
                    f"t(file_id, group_set_id, provider_group_id)) "
                    f"TO '{root}/anthem/group_sets/{fid}.parquet' (FORMAT parquet)")
        con.execute(f"COPY (SELECT * FROM (VALUES ({fid}, 1::BIGINT, '{NET}', 111::BIGINT, "
                    f"'npi', '999')) t(file_id, provider_group_id, network_name, npi, "
                    f"tin_type, tin_value)) TO '{root}/anthem/providers/{fid}.parquet' (FORMAT parquet)")
    if collision:
        price2 = (f"(30, 1, '{NET2}', 'CPT', '99213', 'ffs', 'negotiated', 200.0, "
                  f"'2025-12-31', '11', 'professional', '', 'outpatient', '{SLUG}')")
        con.execute(f"COPY (SELECT * FROM (VALUES {price2}) t({cols})) "
                    f"TO '{root}/anthem/prices/net={SLUG}/30.parquet' (FORMAT parquet)")
        con.execute("COPY (SELECT * FROM (VALUES (30, 1::BIGINT, 1::BIGINT)) "
                    "t(file_id, group_set_id, provider_group_id)) "
                    f"TO '{root}/anthem/group_sets/30.parquet' (FORMAT parquet)")
        con.execute(f"COPY (SELECT * FROM (VALUES (30, 1::BIGINT, '{NET2}', 111::BIGINT, "
                    f"'npi', '999')) t(file_id, provider_group_id, network_name, npi, "
                    f"tin_type, tin_value)) TO '{root}/anthem/providers/30.parquet' (FORMAT parquet)")

    con.execute(f"COPY (SELECT * FROM (VALUES ('CPT', '99213', 'Office visit', 'x'), "
                f"('CPT', '99215', 'Long visit', 'x'), ('CPT', '88888', 'Panel', 'y')) "
                f"t(billing_code_type, billing_code, name, description)) "
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
        "SELECT file_id, billing_code, negotiated_rate, scope, is_sentinel, source_kind "
        f"FROM read_parquet('{serving}/rates/**/*.parquet') ORDER BY billing_code, file_id, negotiated_rate"
    ).fetchall()


def test_scope_matches_serving():
    # the one guard against OUTPATIENT_PROF drifting from serving/data_sources.py
    assert OUTPATIENT_PROF == outpatient_scope("")


def test_source_kind_from_plan_count(out):
    con, serving = out
    kinds = dict(con.execute(
        f"SELECT DISTINCT file_id, source_kind FROM read_parquet('{serving}/rates/**/*.parquet')"
    ).fetchall())
    assert kinds == {10: "plan_specific", 20: "shared"}


def test_empty_plan_counts_tags_everything_shared(tmp_path, monkeypatch):
    # plan_counts={} (not None) must skip the DATABASE_URL probe and tag every
    # row 'shared' without raising — executemany on an empty list is a DuckDB
    # error (regression: the api conftest fixture hit this).
    for v in ("ANTHEM_DIR", "NPPES_DIR", "REFERENCE_DIR", "CMS_DIR", "SERVING_DIR",
              "DATABASE_URL", "TEST_DATABASE_URL", "DUCKDB_TMP"):
        monkeypatch.delenv(v, raising=False)
    root = str(tmp_path)
    _raw(root)
    serving = f"{root}/serving"
    build(root, serving, networks=[SLUG], test=False, plan_counts={})
    con = duckdb.connect()
    kinds = {r[0] for r in con.execute(
        f"SELECT DISTINCT source_kind FROM read_parquet('{serving}/rates/**/*.parquet')"
    ).fetchall()}
    assert kinds == {"shared"}


def test_rates_is_price_grain(out):
    con, serving = out
    # price grain: one row per raw price, not fanned out to provider groups.
    r99213 = sorted((r[0], r[2]) for r in _rates(con, serving) if r[1] == "99213")
    assert r99213 == [(10, 100.0), (20, 60.0), (20, 100.0)]
    cols = {r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{serving}/rates/**/*.parquet')").fetchall()}
    assert "group_set_id" in cols and "provider_group_id" not in cols


def test_scope_and_sentinel(out):
    con, serving = out
    rows = _rates(con, serving)
    assert all(r[3] == "outpatient_prof" for r in rows)
    s = {r[1]: r[4] for r in rows}
    assert s["88888"] is True and s["99213"] is False


def test_rate_hist_roster_weighted(out):
    con, serving = out
    # group_set 1 holds 2 provider groups, so n = 2 * n_rates for each bucket.
    rows = con.execute(
        "SELECT modifier, n, n_rates FROM "
        f"read_parquet('{serving}/rate_hist.parquet') "
        "WHERE billing_code = '99213' AND scope = 'outpatient_prof'").fetchall()
    assert rows and all(n == 2 * nr for _, n, nr in rows)


def test_dims_written(out):
    con, serving = out
    for f in TABLES:
        assert os.path.exists(f"{serving}/{f}")
    row = con.execute(
        "SELECT service_lines, address_line1, city, taxonomy_code, last_name FROM "
        f"read_parquet('{serving}/provider_dim.parquet')").fetchone()
    assert row == ("pcp", "1 St", "Atlanta", "207Q00000X", "Doe")
    # the group -> network attribution, slugged exactly like the rates partition
    assert con.execute(f"SELECT DISTINCT file_id, provider_group_id, net, network_name "
                       f"FROM read_parquet('{serving}/group_networks.parquet') ORDER BY 1"
                       ).fetchall() == [(10, 1, SLUG, NET), (20, 1, SLUG, NET)]
    # no DAC / CMS inputs in this fixture -> empty but well-typed tables
    ev = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{serving}/evidence.parquet')").fetchall()
    assert [c[0] for c in ev] == ["npi", "billing_code", "tier", "prevalence", "year", "tot_srvcs",
                                  "tot_benes", "tot_bene_days", "avg_mdcr_allowed", "is_drug"]
    assert con.execute(f"SELECT COUNT(*) FROM read_parquet('{serving}/provider_affiliations.parquet')").fetchone()[0] == 0


def test_manifest(out):
    con, serving = out
    with open(f"{serving}/manifest.json") as f:
        m = json.load(f)
    assert m["networks"] == [SLUG] and m["partial"] is True
    assert m["inputs"]["nppes"] is True and m["inputs"]["cms_utilization"] is False
    assert m["rows"]["rates"] == 44 and m["rows"]["group_sets"] == 4
    assert m["typical_threshold"] == 0.03


def test_rate_hist_carries_is_sentinel(out):
    con, serving = out
    rows = dict(con.execute(
        "SELECT billing_code, bool_or(is_sentinel) FROM "
        f"read_parquet('{serving}/rate_hist.parquet') GROUP BY 1").fetchall())
    assert rows["88888"] is True and rows["99213"] is False


def test_cross_network_rollup_from_hist(out):
    con, serving = out
    # 99213 global outpatient: $100 (file 10 + file 20) and $60 (file 20), each
    # roster-weighted x2 -> n_groups 6; CDF median crosses in the $100 bucket.
    r = con.execute(
        "SELECT n_groups, min_rate, median, max_rate FROM "
        f"read_parquet('{serving}/cross_network_rollup.parquet') "
        "WHERE billing_code = '99213'").fetchone()
    assert r == (6, 50.0, 112.5, 125.0)
    # the sentinel-only code never reaches the rollup
    assert con.execute("SELECT COUNT(*) FROM "
                       f"read_parquet('{serving}/cross_network_rollup.parquet') "
                       "WHERE billing_code = '88888'").fetchone()[0] == 0


def test_network_name_collision_kept_distinct(tmp_path, monkeypatch):
    # Two real, distinct Anthem network_name values can slugify to the same
    # `net` partition key (differ only in punctuation — a real corpus fact
    # caught by #96's by_network golden test dropping a network from its
    # count). rate_hist / cross_network_rollup must key on (net, network_name),
    # not net alone, or one network's rates silently absorb the other's.
    for v in ("ANTHEM_DIR", "NPPES_DIR", "REFERENCE_DIR", "CMS_DIR", "SERVING_DIR",
              "DATABASE_URL", "TEST_DATABASE_URL", "DUCKDB_TMP"):
        monkeypatch.delenv(v, raising=False)
    root = str(tmp_path)
    _raw(root, collision=True)
    serving = f"{root}/serving"
    build(root, serving, networks=[SLUG], test=False, plan_counts={})
    con = duckdb.connect()

    hist_nets = {r[0] for r in con.execute(
        "SELECT DISTINCT network_name FROM "
        f"read_parquet('{serving}/rate_hist.parquet') "
        "WHERE billing_code = '99213' AND scope = 'outpatient_prof' AND modifier = ''"
    ).fetchall()}
    assert hist_nets == {NET, NET2}

    rollup = con.execute(
        "SELECT network_name, n_groups, min_rate, max_rate FROM "
        f"read_parquet('{serving}/cross_network_rollup.parquet') "
        "WHERE billing_code = '99213' ORDER BY network_name"
    ).fetchall()
    assert [r[0] for r in rollup] == [NET, NET2]
    # NET's rollup is unchanged by NET2 sharing its partition (still $50-$125,
    # same as test_cross_network_rollup_from_hist); NET2 is file 30 alone —
    # one group, one rate ($200), a $25 bucket of its own.
    net_row = next(r for r in rollup if r[0] == NET)
    net2_row = next(r for r in rollup if r[0] == NET2)
    assert (net_row[1], net_row[2], net_row[3]) == (6, 50.0, 125.0)
    assert (net2_row[1], net2_row[2], net2_row[3]) == (1, 200.0, 225.0)
