"""The one DuckDB database the routes share (application/serving/data_sources.py db()).

Each test swaps in a fresh database, and monkeypatch puts the shared one back,
so the other tests' database is left as it was.
"""
import duckdb
import pytest

from application.serving import data_sources


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    """db() with no database yet, spilling under tmp_path; set `_DUCK_MEM` to test."""
    monkeypatch.setattr(data_sources, "_CONN", None)
    monkeypatch.setattr(data_sources, "_DUCK_TMP", str(tmp_path / "spill"))
    return monkeypatch


def test_the_settings_are_in_force(fresh, tmp_path):
    fresh.setattr(data_sources, "_DUCK_MEM", "1GiB")
    got = data_sources.db().execute(
        "SELECT current_setting('memory_limit'), current_setting('temp_directory')").fetchone()
    assert got == ("1.0 GiB", str(tmp_path / "spill"))


def test_a_refused_setting_is_raised_not_skipped(fresh):
    # DuckDB's own defaults are most of the machine's memory and a spill dir in
    # the working directory, so serving without the setting would be worse than failing.
    fresh.setattr(data_sources, "_DUCK_MEM", "lots")
    with pytest.raises(duckdb.Error):
        data_sources.db()
    assert data_sources._CONN is None, "a database without its settings was kept for the next request"
