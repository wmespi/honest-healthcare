"""The corpus the table tests (build/tables/) build from: a small insurer and
reference corpus in a tmp dir, with a targets file of its own."""
import os

import duckdb
import pytest

from build import tables

TARGETS = """
targets:
  - name: Blue Value
    exchange_networks: ["45334-GAN001"]
    network_patterns: ["GA Blue Value HIX*"]
  - name: Pathway
    exchange_networks: ["49046-GAN008"]
    network_patterns: ["Pathway PCP Copay Choice*"]
"""


class Corpus:
    def __init__(self, root):
        self.root = root
        self.targets = f"{root}/targets.yaml"

    def rate(self, fid, label):
        """Add one insurer rate in file `fid` under network label `label`."""
        slug = label.strip().lower().replace(" ", "-")
        os.makedirs(f"{self.root}/anthem/prices/net={slug}", exist_ok=True)
        duckdb.connect().execute(f"""
            COPY (SELECT {fid}::BIGINT AS file_id, 1::BIGINT AS group_set_id, '{label}' AS network_name,
                         'CPT' AS billing_code_type, '70551' AS billing_code, 250.0 AS negotiated_rate)
            TO '{self.root}/anthem/prices/net={slug}/{fid}.parquet' (FORMAT parquet)
        """)

    def remove_rate(self, fid, label):
        os.remove(f"{self.root}/anthem/prices/net={label.strip().lower().replace(' ', '-')}/{fid}.parquet")

    def add_target(self, text):
        """Add a target to the targets file, as YAML list items."""
        with open(self.targets, "a") as f:
            f.write(text)

    def build(self, out="serving"):
        return tables.build(self.root, f"{self.root}/{out}", targets_path=self.targets)

    def rows(self, table, out="serving"):
        return duckdb.connect().execute(
            f"SELECT * FROM read_parquet('{self.root}/{out}/{table}.parquet')").fetchall()


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    """Blue Value in file 21057, Pathway and an untargeted network in file 701, and
    Georgia's network list."""
    monkeypatch.delenv("DUCKDB_TMP", raising=False)
    c = Corpus(str(tmp_path))
    c.rate(21057, "GA Blue Value HIX Individual Network")
    c.rate(701, "Pathway PCP Copay Choice")
    c.rate(701, "TRADITIONAL HEALTH PLAN")
    os.makedirs(f"{c.root}/reference")
    duckdb.connect().execute(f"""
        COPY (SELECT * FROM (VALUES
            ('45334-GAN001', '45334', 'Blue Value Individual Network HMO'),
            ('49046-GAN008', '49046', 'Pathway PCP Copay Choice'),
            ('58081-GAN001', '58081', 'Individual Georgia HMO')) t(network_id, issuer_id, name))
        TO '{c.root}/reference/exchange_networks_ga.parquet' (FORMAT parquet)
    """)
    (tmp_path / "targets.yaml").write_text(TARGETS)
    return c
