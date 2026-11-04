"""What every table gets (build/table.py) and how the build runs them
(build/tables/__init__.py)."""
import hashlib
import os
import subprocess
import sys

import pytest

from build.tables.networks import Networks

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def test_two_builds_write_the_same_bytes(corpus):
    corpus.build("a")
    corpus.build("b")
    for name in sorted(os.listdir(f"{corpus.root}/a")):
        a, b = (hashlib.sha256(open(f"{corpus.root}/{d}/{name}", "rb").read()).hexdigest() for d in "ab")
        assert a == b, name


def test_a_failed_check_leaves_the_last_build_as_it_was(corpus):
    corpus.build()
    before = open(f"{corpus.root}/serving/networks.parquet", "rb").read()
    corpus.rate(21000, "GA Blue Value HIX Individual Network")
    with pytest.raises(SystemExit):
        corpus.build()
    assert open(f"{corpus.root}/serving/networks.parquet", "rb").read() == before


def test_a_table_whose_columns_drift_from_its_definition_stops_the_build(corpus, monkeypatch):
    monkeypatch.setattr(Networks, "columns", {"network_id": "VARCHAR", "label": "VARCHAR"})
    with pytest.raises(SystemExit, match="networks: columns .* but the table says"):
        corpus.build()


def test_the_tables_import_nothing_from_serving():
    r = subprocess.run([sys.executable, "-c",
                        "import sys, build.tables; "
                        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'serving'))"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"
