"""The build-only imaging_centers table (build/inputs/imaging_centers.py)."""
import pytest


def test_imaging_centers_are_nuccs_radiology_and_mri_clinic_codes(corpus):
    corpus.taxonomy("261QM1200X", "Clinic/Center", "Magnetic Resonance Imaging (MRI)")
    assert corpus.inputs().execute("SELECT taxonomy_code FROM imaging_centers ORDER BY 1").fetchall() == [
        ("261QM1200X",), ("261QR0200X",)]  # not a multi-specialty clinic, nor a radiologist


def test_a_rule_that_matches_no_nucc_code_stops_the_build(corpus):
    corpus.taxonomies.clear()
    corpus.taxonomy("261QM1300X", "Clinic/Center", "Multi-Specialty")
    with pytest.raises(SystemExit, match="imaging_centers: no NUCC code is a radiology or MRI clinic"):
        corpus.build()
