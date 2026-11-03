"""Hermetic test for the find-care tables (build/find_care.py): writes a small raw
and reference corpus to a tmp dir, runs the build against it, and checks each
table's rows, its key checks, and that two builds write the same bytes."""
import hashlib
import os
import subprocess
import sys

import duckdb
import pytest

from build import find_care

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

TARGETS = """
targets:
  - name: Blue Value
    exchange_networks: ["45334-GAN001"]
    network_patterns: ["GA Blue Value HIX*"]
  - name: Pathway
    exchange_networks: ["49046-GAN008"]
    network_patterns: ["Pathway PCP Copay Choice*"]
"""


def _rate(con, root, fid, label):
    """One insurer rate in file `fid` under network label `label`."""
    slug = label.lower().replace(" ", "-")
    os.makedirs(f"{root}/anthem/prices/net={slug}", exist_ok=True)
    con.execute(f"""
        COPY (SELECT {fid}::BIGINT AS file_id, 1::BIGINT AS group_set_id, '{label}' AS network_name,
                     'CPT' AS billing_code_type, '70551' AS billing_code, 250.0 AS negotiated_rate)
        TO '{root}/anthem/prices/net={slug}/{fid}.parquet' (FORMAT parquet)
    """)


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    """Blue Value in file 21057, Pathway and an untargeted network in file 701, and
    Georgia's network list."""
    monkeypatch.delenv("DUCKDB_TMP", raising=False)
    root = str(tmp_path)
    con = duckdb.connect()
    _rate(con, root, 21057, "GA Blue Value HIX Individual Network")
    _rate(con, root, 701, "Pathway PCP Copay Choice")
    _rate(con, root, 701, "TRADITIONAL HEALTH PLAN")
    os.makedirs(f"{root}/reference")
    con.execute(f"""
        COPY (SELECT * FROM (VALUES
            ('45334-GAN001', '45334', 'Blue Value Individual Network HMO'),
            ('49046-GAN008', '49046', 'Pathway PCP Copay Choice'),
            ('58081-GAN001', '58081', 'Individual Georgia HMO')) t(network_id, issuer_id, name))
        TO '{root}/reference/exchange_networks_ga.parquet' (FORMAT parquet)
    """)
    (tmp_path / "targets.yaml").write_text(TARGETS)
    return root


def _build(root, out="serving"):
    return find_care.build(root, f"{root}/{out}", targets_path=f"{root}/targets.yaml")


def _rows(root, table, out="serving"):
    return duckdb.connect().execute(f"SELECT * FROM read_parquet('{root}/{out}/{table}.parquet')").fetchall()


def test_networks_are_the_targets_named_by_georgia(corpus):
    assert _build(corpus) == {"networks": 2}
    assert _rows(corpus, "networks") == [
        ("45334-GAN001", "Blue Value Individual Network HMO"),
        ("49046-GAN008", "Pathway PCP Copay Choice")]


def test_two_builds_write_the_same_bytes(corpus):
    _build(corpus, "a")
    _build(corpus, "b")
    for name in sorted(os.listdir(f"{corpus}/a")):
        a, b = (hashlib.sha256(open(f"{corpus}/{d}/{name}", "rb").read()).hexdigest() for d in "ab")
        assert a == b, name


def test_a_second_file_with_the_networks_label_stops_the_build(corpus):
    # GA_JBKEMED0001 carries Blue Value's label but prices a plan no one can buy.
    _rate(duckdb.connect(), corpus, 21000, "GA Blue Value HIX Individual Network")
    with pytest.raises(SystemExit, match=r"files \[21000, 21057\].*one file prices 45334-GAN001"):
        _build(corpus)


def test_a_network_no_parsed_file_prices_stops_the_build(corpus):
    os.remove(f"{corpus}/anthem/prices/net=pathway-pcp-copay-choice/701.parquet")
    with pytest.raises(SystemExit, match="nothing prices 49046-GAN008; run `make parse`"):
        _build(corpus)


def test_a_network_georgia_doesnt_list_stops_the_build(corpus):
    with open(f"{corpus}/targets.yaml", "a") as f:
        f.write('  - name: Typo\n    exchange_networks: ["45334-GAN009"]\n'
                '    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match="45334-GAN009 isn't a network in Georgia's exchange files"):
        _build(corpus)


def test_a_missing_reference_file_names_the_step_that_makes_it(corpus):
    os.remove(f"{corpus}/reference/exchange_networks_ga.parquet")
    with pytest.raises(SystemExit, match="run `make reference STEP=exchange-plans`"):
        _build(corpus)


def test_two_targets_naming_one_network_stop_the_build(corpus):
    with open(f"{corpus}/targets.yaml", "a") as f:
        f.write('  - name: Blue Value again\n    exchange_networks: ["45334-GAN001"]\n'
                '    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match=r"targets: the key \(network_id\) repeats"):
        _build(corpus)


def test_a_blank_network_in_the_targets_file_stops_the_build(corpus):
    with open(f"{corpus}/targets.yaml", "a") as f:
        f.write('  - name: Blank\n    exchange_networks:\n      -\n'
                '    network_patterns: ["GA Blue Value HIX*"]\n')
    with pytest.raises(SystemExit, match=r"targets: 1 rows with an empty key \(network_id\)"):
        _build(corpus)


def test_a_table_whose_columns_drift_from_its_definition_stops_the_build(corpus, monkeypatch):
    monkeypatch.setattr(find_care._Networks, "columns", {"network_id": "VARCHAR", "label": "VARCHAR"})
    with pytest.raises(SystemExit, match="networks: columns .* but the table says"):
        _build(corpus)


def test_the_targets_file_names_networks_georgia_lists():
    """etl/targets.yaml itself: every target pairs exchange networks with labels."""
    for name, nets, patterns in find_care.read_targets():
        assert nets and patterns, name


def test_find_care_imports_nothing_from_serving():
    r = subprocess.run([sys.executable, "-c",
                        "import sys, build.find_care; "
                        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'serving'))"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"
