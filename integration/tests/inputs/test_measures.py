"""integration/measures.yaml (integration/inputs/measures.py)."""
import copy

import pytest

from integration.inputs.measures import read_measures


def _read(corpus, change):
    model = copy.deepcopy(corpus.measures)
    change(model["measures"])
    corpus.measures = model
    corpus.write_measures()
    return read_measures(corpus.measures_file, corpus.families_file)


@pytest.mark.parametrize("change, error", [
    (lambda m: m[0].update(extra=1), "OP-32 has fields the build doesn't use"),
    (lambda m: m[1].update(measure="OP-32"), "measure OP-32 repeats"),
    (lambda m: m[0].update(about=""), "OP-32 has no about"),
    (lambda m: m[0].update(source="other"), "OP-32's source is 'other'"),
    (lambda m: m[0].update(subject="clinician"), "OP-32's source hospital-measures measures a place"),
    (lambda m: m[0].update(unit="dollars"), "OP-32's unit is 'dollars'"),
    (lambda m: m[0].update(category="Colonoscopy"), "OP-32 needs a category exactly when"),
    (lambda m: m[4].pop("category"), "volume-colonoscopy needs a category exactly when"),
    (lambda m: m[0].update(fits=[{"family": "cardiology"}]), "OP-32 fits family cardiology"),
    (lambda m: m[0].update(fits=[{"family": "mri"}, {"family": "mri"}]), "OP-32 fits family mri twice"),
    (lambda m: m[0].update(fits=[{"family": "mri", "services": []}]), "services for mri must be a list"),
], ids=["an unused field", "a repeat", "no about", "an unknown source", "a subject its source doesn't measure",
        "an unknown unit", "a category on a place measure", "a volume with no category", "an unknown family",
        "a family twice", "an empty list of services"])
def test_a_measures_file_that_breaks_a_rule_stops_the_build(corpus, change, error):
    with pytest.raises(SystemExit, match=error):
        _read(corpus, change)


def test_a_measure_may_fit_no_family_yet(corpus):
    assert _read(corpus, lambda m: m[0].update(fits=[]))[0]["fits"] == []


def test_a_service_that_isnt_in_the_measures_family_stops_the_build(corpus):
    corpus.measures["measures"][2]["fits"][0]["services"] = ["mammogram-screening"]
    corpus.write_measures()
    with pytest.raises(SystemExit, match="ASC-12 fits service mammogram-screening, which isn't a service in mri"):
        corpus.inputs()


def test_a_volume_category_cms_doesnt_have_stops_the_build(corpus):
    corpus.measures["measures"][4]["category"] = "Heart transplant"
    corpus.write_measures()
    with pytest.raises(SystemExit, match="no category .*Heart transplant"):
        corpus.build()


def test_the_real_measures_file_loads_with_each_measure_fitting_a_family_or_none_yet():
    listed = read_measures()
    assert {m["source"] for m in listed} == {"hospital-measures", "asc-measures", "clinician-volume"}
    assert [m["measure"] for m in listed if not m["fits"]] == ["H_STAR_RATING"]
