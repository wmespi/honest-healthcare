"""`make build` (integration/build.py): the find-care tables and the manifest `GET /` reads."""
import json

import pytest

from integration.build import build


def make(corpus):
    corpus.flush()
    return build(corpus.root, f"{corpus.root}/serving", corpus.services_file, corpus.hospital_plans_file,
                 corpus.families_file)


def test_the_build_writes_the_find_care_tables_and_says_when(corpus):
    rows = make(corpus)
    with open(f"{corpus.root}/serving/manifest.json") as f:
        manifest = json.load(f)
    assert manifest["rows"] == rows and rows["prices"] == 7
    assert manifest["built_at"].endswith("+00:00")


def test_a_failed_check_writes_no_manifest(corpus):
    corpus.service("mri-elbow", "72148")  # a second service on the same code
    with pytest.raises(SystemExit):
        make(corpus)
    with pytest.raises(FileNotFoundError):
        open(f"{corpus.root}/serving/manifest.json")
