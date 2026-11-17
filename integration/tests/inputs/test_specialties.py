"""integration/specialties.yaml (integration/inputs/specialties.py)."""
import copy

import pytest
import yaml

from integration.inputs.specialties import read_specialties


def _read(corpus, change=None):
    model = copy.deepcopy(corpus.specialties)
    if change:
        change(model["specialties"])
    corpus.specialties = model
    corpus.write_specialties()
    return read_specialties(corpus.specialties_file, corpus.families_file)


@pytest.mark.parametrize("change, error", [
    (lambda s: s[1]["taxonomies"].append("207Q00000X"), "taxonomy 207Q00000X is in primary_care and in heart"),
    (lambda s: s[0].update(benefit="preventive"), "primary_care's benefit is 'preventive'"),
    (lambda s: s[0].update(benefit="vision"), "primary_care's benefit is 'vision'"),
    (lambda s: s[1].update(specialty="primary_care"), "specialty primary_care repeats"),
    (lambda s: s[0].update(taxonomies=[]), "primary_care's taxonomies must be a list"),
    (lambda s: s[0].update(extra=1), "has fields the build doesn't use"),
], ids=["a taxonomy in two", "a benefit that is no kind of visit", "an unknown benefit", "a repeat", "no taxonomies", "an unused field"])
def test_a_specialties_file_that_breaks_a_rule_stops_the_build(corpus, change, error):
    with pytest.raises(SystemExit, match=error):
        _read(corpus, change)


def test_a_code_nucc_no_longer_lists_stops_the_build(corpus):
    corpus.specialties["specialties"][0]["taxonomies"].append("207QZ9999X")
    corpus.write_specialties()
    with pytest.raises(SystemExit, match="NUCC no longer lists .*207QZ9999X"):
        corpus.inputs()


def test_the_real_specialties_file_loads_and_each_benefit_is_one_families_yaml_declares():
    listed = read_specialties()
    assert listed[0]["specialty"] == "primary_care"
    assert {s["benefit"] for s in listed} == {"primary_care", "specialist"}
