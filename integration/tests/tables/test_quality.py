"""quality.parquet and families.measures (integration/tables/quality.py)."""
from integration.tests.corpus import office_visit_practice


def quality(corpus):
    return {(r[0], r[1], r[2]): r for r in corpus.rows("quality")}


def test_a_hospitals_measure_lands_on_its_ccn_with_cms_comparison_in_plain_words(corpus):
    corpus.build()
    row = quality(corpus)[("110079", "place", "OP-32")]
    assert row[3:] == ("Hospital visits after a colonoscopy", "Lower is better.", "CMS hospital outpatient measures",
                       13.9, "13.9 per 1,000", "No different than the national rate", "2022 to 2024", 1,
                       ["mri-lower-back"])


def test_a_hospital_measure_with_no_cms_comparison_says_the_national_value(corpus):
    corpus.build()
    row = quality(corpus)[("110079", "place", "OP-39")]
    assert (row[6], row[7], row[8]) == (9.5, "9.5%", "The national average is 9%")


def test_a_measure_with_neither_a_score_nor_a_comparison_has_no_row(corpus):
    corpus.build()  # Hamilton has too few cases for OP-32
    assert ("110001", "place", "OP-32") not in quality(corpus)


def test_a_surgery_centers_measure_lands_on_the_place_its_npi_bills_from(corpus):
    corpus.build()
    rows = quality(corpus)
    assert rows[("1000000001", "place", "ASC-12")][3:] == (
        "Hospital visits after a colonoscopy", "Lower is better.", "CMS surgery center measures", 8.2,
        "8.2 per 1,000", "Better than the national rate", "2025", 3, ["mri-lower-back"])
    assert rows[("1000000001", "place", "ASC-11")][7:9] == ("93%", "The national average is 78.8%")


def test_a_surgery_center_that_bills_from_no_place_of_ours_or_from_a_hospital_has_no_row(corpus):
    corpus.build()  # 1000000999 bills nothing of ours, 1000000004 bills from Grady's building
    assert {k for k in quality(corpus) if k[2].startswith("ASC")} == {
        ("1000000001", "place", "ASC-12"), ("1000000001", "place", "ASC-11")}


def test_several_npis_billing_from_one_place_give_it_one_row_per_measure(corpus):
    corpus.asc_measure(1000000009, "ASC-12", 6.0, "Better Than the National Rate")
    corpus.npi(1000000009, "MIDTOWN IMAGING PC", "550 PEACHTREE ST NE", "ATLANTA", "303082233")
    corpus.roster("45334-GAN001", 9, 9, "1000000009", 2000000009)
    corpus.scan_price("45334-GAN001", 9, "72148", all_in=500, scan=300, reading=200)
    corpus.build()
    assert quality(corpus)[("1000000001", "place", "ASC-12")][6] == 8.2  # the lowest NPI


def test_a_volume_lands_only_for_a_displayed_clinician_in_providers(corpus):
    corpus.build()
    rows = {k: r for k, r in quality(corpus).items() if k[1] == "clinician"}
    assert list(rows) == [("1000000005", "clinician", "volume-colonoscopy")]  # not a hidden, absent or other row
    assert rows[("1000000005", "clinician", "volume-colonoscopy")][3:] == (
        "Colonoscopies done", "More suggests practice.", "CMS Doctors and Clinicians", 57.0, "57 procedures",
        None, None, 5, ["mri-lower-back"])


def test_a_volume_cms_gives_as_a_band_reads_as_one_with_its_low_end_as_the_score(corpus):
    corpus.volume(1000000005, "Colonoscopy", "1-10")
    corpus.build()
    row = quality(corpus)[("1000000005", "clinician", "volume-colonoscopy")]
    assert (row[6], row[7]) == (1.0, "1 to 10 procedures")


def test_a_measure_fitting_only_some_services_lists_only_those(corpus):
    corpus.service("mri-knee", "73721")
    corpus.build()
    rows = quality(corpus)
    assert rows[("1000000001", "place", "ASC-12")][11] == ["mri-lower-back"]
    assert rows[("110079", "place", "OP-32")][11] == ["mri-knee", "mri-lower-back"]


def test_a_family_lists_the_measures_that_fit_it_in_order_and_none_when_none_does(corpus):
    office_visit_practice(corpus)
    corpus.build()
    assert {r[0]: r[4] for r in corpus.rows("families")} == {
        "office_visits": [], "mammograms": ["OP-39", "ASC-11"], "mri": ["OP-32", "ASC-12", "volume-colonoscopy"]}
