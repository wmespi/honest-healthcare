"""services.parquet (integration/tables/services.py)."""


def test_services_carry_their_family_text_bills_and_medicare_rates(corpus):
    assert corpus.build()["services"] == 2
    rows = {r[0]: r for r in corpus.rows("services")}
    assert set(rows) == {"mammogram-screening", "mri-lower-back"}  # the office visit has no price here
    mammogram, mri = rows["mammogram-screening"], rows["mri-lower-back"]
    assert mammogram[1:11] == ("77067", "mammograms", "Screening mammogram", "scan", "A scan.", ["Contrast dye"],
                               ["Is this everything?"], None, "Usually $0 in network as preventive care.",
                               mammogram[10])
    assert (mammogram[12], mammogram[13]) == (115.0, None) and (mri[12], mri[13]) == (180.0, 243.77)
    assert mammogram[11] == []
    assert mri[2] == "mri" and mri[8] is None and mri[9] is None
    assert [(e["part"], e["label"], e["code"]) for e in mri[11]] == [
        ("anesthesia", "Anesthesia", "00811"), ("pathology", "Lab exam of removed tissue", "88305")]
    # A service's own benefits beat its bill's and its family's.
    assert {tuple(b["benefits"]) for b in mammogram[10]} == {("preventive",)}
    assert {tuple(b["benefits"]) for b in mri[10]} == {("advanced_imaging",)}
    assert [b["part"] for b in mri[10]] == ["all_in", "scan", "reading", "facility"]


def test_families_lists_only_the_families_with_a_priced_service(corpus):
    from integration.tests.corpus import office_visit_practice
    corpus.build()
    assert sorted(r[:5] for r in corpus.rows("families")) == [
        ("mammograms", "Mammograms", 2, [], ["OP-39", "ASC-11"]),
        ("mri", "MRI scans", 3, [], ["OP-32", "ASC-12", "volume-colonoscopy"])]
    office_visit_practice(corpus)
    corpus.build()
    assert sorted(r[:5] for r in corpus.rows("families")) == [
        ("mammograms", "Mammograms", 2, [], ["OP-39", "ASC-11"]),
        ("mri", "MRI scans", 3, [], ["OP-32", "ASC-12", "volume-colonoscopy"]),
        ("office_visits", "Office visits", 1,
         [{"specialty": "primary_care", "name": "Primary care", "benefit": "primary_care"},
          {"specialty": "heart", "name": "Heart", "benefit": "specialist"},
          {"specialty": "skin", "name": "Skin", "benefit": "specialist"}], [])]
