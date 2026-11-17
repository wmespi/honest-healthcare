"""prices.specialties and families.specialties (integration/specialties.yaml)."""
from integration.tests.corpus import BLUE_VALUE, office_visit_practice

VISIT = (BLUE_VALUE, "office-visit-established")


def visit_rows(corpus):
    return [r for r in corpus.rows("prices") if r[:2] == VISIT]


def specialties(corpus, service_id):
    return {r[2]: r[-1] for r in corpus.rows("prices") if r[1] == service_id}


def test_a_row_s_specialties_come_from_its_clinicians_taxonomies_in_the_files_order(corpus):
    office_visit_practice(corpus)
    corpus.build()
    (row,) = visit_rows(corpus)
    assert row[6] == [2000000070, 2000000071, 2000000072]
    assert row[-1] == ["primary_care", "heart"]  # the radiologist counts as none


def test_a_row_whose_clinicians_count_as_none_has_none(corpus):
    office_visit_practice(corpus)
    for npi in (1000000070, 2000000070, 2000000071):
        corpus.npis = [r for r in corpus.npis if r[0] != npi]
    corpus.npi(1000000070, "FAMILY MEDICINE ASSOCIATES PC", "10 PEACHTREE ST NE", "ATLANTA", "303083001",
               taxonomy="193200000X")  # multi-specialty: names no kind
    corpus.npi(2000000070, "OKAFOR", "10 PEACHTREE ST NE", "ATLANTA", "303083001", entity_type="individual",
               taxonomy="2085R0202X")
    corpus.npi(2000000071, "HADDAD", "10 PEACHTREE ST NE", "ATLANTA", "303083001", entity_type="individual",
               taxonomy="2085R0202X")
    corpus.build()
    assert visit_rows(corpus)[0][-1] == []


def test_a_row_of_a_family_that_doesnt_ask_has_none(corpus):
    office_visit_practice(corpus)
    corpus.build()
    assert all(r[-1] == [] for r in corpus.rows("prices") if r[1] != "office-visit-established")
    corpus.family("office_visits", asks_specialty=False)
    corpus.build()
    assert visit_rows(corpus)[0][-1] == []
    assert corpus.rows("families")[-1][3] == []


def test_families_lists_the_specialties_for_office_visits_only(corpus):
    office_visit_practice(corpus)
    corpus.build()
    assert {r[0]: len(r[3]) for r in corpus.rows("families")} == {"mammograms": 0, "mri": 0, "office_visits": 3}


def test_the_build_says_which_clinicians_specialties_count_as_none(corpus, capsys):
    office_visit_practice(corpus)
    corpus.build()
    out = capsys.readouterr().out
    assert "count as no kind of doctor, with their places (1): Diagnostic Radiology Physician 1" in out
    assert "with no kind: 0 of 1" in out


def only_a_generic_nurse_practitioner(corpus, biller_taxonomy):
    """The practice's clinicians are a generic nurse practitioner (no kind) and a radiologist, and
    its billing NPI is registered as `biller_taxonomy`."""
    office_visit_practice(corpus)
    for npi in (1000000070, 2000000070, 2000000071):
        corpus.npis = [r for r in corpus.npis if r[0] != npi]
    corpus.npi(1000000070, "FAMILY MEDICINE ASSOCIATES PC", "10 PEACHTREE ST NE", "ATLANTA", "303083001",
               taxonomy=biller_taxonomy)
    for npi in (2000000070, 2000000071):
        corpus.npi(npi, "NURSE", "10 PEACHTREE ST NE", "ATLANTA", "303083001", entity_type="individual",
                   taxonomy="363L00000X")
    corpus.taxonomy("363L00000X", "Nurse Practitioner", "", "Nurse Practitioner")
    corpus.build()


def test_where_no_clinician_gives_a_kind_the_billers_own_registration_does(corpus):
    only_a_generic_nurse_practitioner(corpus, "207N00000X")  # registered as dermatology
    assert visit_rows(corpus)[0][-1] == ["skin"]


def test_a_biller_registered_as_multi_specialty_names_no_kind(corpus):
    only_a_generic_nurse_practitioner(corpus, "193200000X")
    assert visit_rows(corpus)[0][-1] == []


def test_a_clinician_s_kind_wins_over_the_billers(corpus):
    office_visit_practice(corpus)
    corpus.npis = [r for r in corpus.npis if r[0] != 1000000070]
    corpus.npi(1000000070, "FAMILY MEDICINE ASSOCIATES PC", "10 PEACHTREE ST NE", "ATLANTA", "303083001",
               taxonomy="207N00000X")
    corpus.build()
    assert visit_rows(corpus)[0][-1] == ["primary_care", "heart"]
