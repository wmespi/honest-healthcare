"""The build-only imaging_centers table (build/inputs/imaging_centers.py)."""
import pytest

from build.inputs.imaging_centers import CODES


def test_imaging_centers_are_the_five_codes_as_nucc_lists_them(corpus):
    assert corpus.inputs().execute("SELECT taxonomy_code FROM imaging_centers ORDER BY 1").fetchall() == [
        (c,) for c in sorted(CODES)]


def test_a_code_nucc_no_longer_lists_stops_the_build(corpus):
    corpus.taxonomies[:] = [t for t in corpus.taxonomies if t[0] != "261QR0207X"]
    corpus.taxonomy("261QM1300X", "Clinic/Center", "Multi-Specialty")
    with pytest.raises(SystemExit, match=r"NUCC no longer lists \['261QR0207X'\]"):
        corpus.build()
