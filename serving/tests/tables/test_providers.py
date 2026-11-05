"""providers.parquet (build/tables/providers.py)."""
import pytest

BV, PATHWAY = "45334-GAN001", "49046-GAN008"


def providers(corpus):
    corpus.build()
    return {(r[0], r[1]): r[2:] for r in corpus.rows("providers")}


def person(corpus, npi, last_name, taxonomy="2085R0202X"):
    corpus.npi(npi, last_name, "1 DOCTOR WAY", "ATLANTA", "303082233", entity_type="individual",
               taxonomy=taxonomy)


def test_a_provider_is_listed_where_medicare_shows_them_billing_a_service_priced_there(corpus):
    for npi, last_name in [(2000000001, "OKAFOR"), (2000000002, "LINDQVIST"), (2000000003, "HADDAD"),
                           (2000000004, "PRESCOTT"), (2000000007, "MBEKI"), (2000000008, "IVERSON")]:
        person(corpus, npi, last_name)
    corpus.billed(1000000001, "72148", "O")  # an organization's own NPI isn't a person
    assert providers(corpus) == {
        (1000000005, BV): ("PAT SMITH", "Diagnostic Radiology Physician", 87.5, ["1000000005"]),
        (2000000001, BV): ("PAT OKAFOR", "Diagnostic Radiology Physician", None, ["1000000001"]),
        (2000000002, BV): ("PAT LINDQVIST", "Diagnostic Radiology Physician", None, ["1000000002"]),
        # He reads at Grady's address, so he's at the hospital.
        (2000000004, BV): ("PAT PRESCOTT", "Diagnostic Radiology Physician", None, ["110079"])}
    # 2000000003's group isn't on the map, 2000000007's has no place, and Medicare
    # shows 2000000008 billing nothing.


def test_a_provider_is_listed_on_each_network_only_where_that_networks_roster_has_them(corpus):
    # Pathway's roster puts the doctor who works at Midtown on Blue Value with
    # Grady's reading group instead.
    person(corpus, 2000000001, "OKAFOR")
    corpus.roster(701, 4, 4, "1000000004", 2000000001)
    corpus.scan_price(701, "Pathway PCP Copay Choice", 4, "72148", reading=210)
    rows = providers(corpus)
    assert rows[(2000000001, BV)][-1] == ["1000000001"]
    assert rows[(2000000001, PATHWAY)][-1] == ["110079"]


def test_a_provider_whose_specialty_code_nucc_doesnt_name_stops_the_build(corpus):
    person(corpus, 2000000001, "OKAFOR", taxonomy="208VP0014X")
    with pytest.raises(SystemExit, match="providers: 2000000001 has no name or no specialty"):
        corpus.build()
