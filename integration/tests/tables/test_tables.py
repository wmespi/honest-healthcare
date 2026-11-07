"""What every table gets (integration/table.py) and how the build runs them
(integration/tables/__init__.py)."""
import hashlib
import os
import subprocess
import sys

import pytest

from integration.tables.networks import Networks
from integration.tables.providers import Providers

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
    corpus.rate("45334-GAN009")  # a network Georgia doesn't list
    with pytest.raises(SystemExit):
        corpus.build()
    assert open(f"{corpus.root}/serving/networks.parquet", "rb").read() == before


def test_a_failed_write_leaves_every_table_of_the_last_build_as_it_was(corpus, monkeypatch):
    corpus.build()
    serving = f"{corpus.root}/serving"
    before = {name: open(f"{serving}/{name}", "rb").read() for name in os.listdir(serving)}
    corpus.zip("30399", 33.75, -84.39)  # a change to zips, which is written before providers

    def disk_full(self, con, out_dir):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Providers, "stage", disk_full)  # the last table written
    with pytest.raises(OSError, match="No space left"):
        corpus.build()
    assert sorted(os.listdir(serving)) == sorted(before), "a temp file or the spill dir was left behind"
    for name, data in before.items():
        assert open(f"{serving}/{name}", "rb").read() == data, f"{name} was replaced"


def test_a_table_whose_columns_drift_from_its_definition_stops_the_build(corpus, monkeypatch):
    monkeypatch.setattr(Networks, "columns", {"network_id": "VARCHAR", "label": "VARCHAR"})
    with pytest.raises(SystemExit, match="networks: columns .* but the table says"):
        corpus.build()


def test_the_build_imports_nothing_from_ingestion_or_the_app():
    r = subprocess.run([sys.executable, "-c",
                        "import sys, integration.build; "
                        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('application', 'ingestion')))"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"
