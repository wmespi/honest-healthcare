"""services.parquet (integration/tables/services.py)."""


def test_services_carry_their_family_text_bills_and_medicare_rates(corpus):
    assert corpus.build()["services"] == 2
    rows = {r[0]: r for r in corpus.rows("services")}
    assert set(rows) == {"mammogram-screening", "mri-lower-back"}  # the office visit has no price here
    mammogram, mri = rows["mammogram-screening"], rows["mri-lower-back"]
    assert mammogram[1:10] == ("77067", "mammograms", "Screening mammogram", "scan", "A scan.", ["Contrast dye"],
                               ["Is this everything?"], "Usually $0 in network as preventive care.", mammogram[9])
    assert (mammogram[10], mammogram[11]) == (115.0, None) and (mri[10], mri[11]) == (180.0, 243.77)
    assert mri[2] == "mri" and mri[8] is None
    # A service's own benefits beat its bill's and its family's.
    assert {tuple(b["benefits"]) for b in mammogram[9]} == {("preventive",)}
    assert {tuple(b["benefits"]) for b in mri[9]} == {("advanced_imaging",)}
    assert [b["part"] for b in mri[9]] == ["all_in", "scan", "reading", "facility"]


def test_families_lists_only_the_families_with_a_priced_service(corpus):
    from integration.tests.corpus import office_visit_practice
    corpus.build()
    assert sorted(corpus.rows("families")) == [("mammograms", "Mammograms", 2), ("mri", "MRI scans", 3)]
    office_visit_practice(corpus)
    corpus.build()
    assert sorted(corpus.rows("families")) == [("mammograms", "Mammograms", 2), ("mri", "MRI scans", 3),
                                               ("office_visits", "Office visits", 1)]
