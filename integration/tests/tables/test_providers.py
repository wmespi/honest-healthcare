"""providers.parquet (integration/tables/providers.py)."""
import pytest

from integration.tests.corpus import office_visit_practice


def person(corpus, npi, last_name, taxonomy="2085R0202X"):
    corpus.npi(npi, last_name, "1 DOCTOR WAY", "ATLANTA", "303082233", entity_type="individual",
               taxonomy=taxonomy)


def test_each_clinician_a_price_lists_has_a_name_specialty_and_score(corpus):
    person(corpus, 2000000001, "OKAFOR")
    person(corpus, 2000000003, "HADDAD")  # his group isn't on the map, so no price lists him
    corpus.build()
    assert corpus.rows("providers") == [
        (1000000005, "PAT SMITH", "Diagnostic Radiology Physician", 87.5, None),
        (2000000001, "PAT OKAFOR", "Diagnostic Radiology Physician", None, None)]


def test_a_provider_has_the_kind_of_doctor_its_specialty_code_counts_as_or_none(corpus):
    office_visit_practice(corpus)
    corpus.build()
    assert {r[0]: (r[2], r[4]) for r in corpus.rows("providers") if r[0] > 2000000069} == {
        2000000070: ("Family Medicine Physician", "primary_care"),
        2000000071: ("Cardiovascular Disease Physician", "heart"),
        2000000072: ("Diagnostic Radiology Physician", None)}


def test_a_provider_whose_specialty_code_nucc_doesnt_name_stops_the_build(corpus):
    person(corpus, 2000000001, "OKAFOR", taxonomy="208VP0014X")
    with pytest.raises(SystemExit, match="providers: 2000000001 has no name or no specialty"):
        corpus.build()
