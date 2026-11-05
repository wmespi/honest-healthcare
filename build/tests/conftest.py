"""The `corpus` fixture the build's tests share (corpus.py builds it)."""
import pytest

from build.tests.corpus import default_corpus


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    """The default corpus (default_corpus) in a tmp dir, for one test to change."""
    monkeypatch.delenv("DUCKDB_TMP", raising=False)
    return default_corpus(str(tmp_path))
