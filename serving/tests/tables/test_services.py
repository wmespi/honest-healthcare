"""services.parquet (build/tables/services.py)."""


def test_services_carry_their_text_and_medicare_rates(corpus):
    assert corpus.build()["services"] == 2
    assert corpus.rows("services") == [
        ("mammogram-screening", "77067", "Screening mammogram", "scan", "A scan.", ["Contrast dye"],
         ["Is this everything?"], "Usually $0 in network as preventive care.", 115.0, None),
        ("mri-lower-back", "72148", "Lower-back MRI", "scan", "A scan.", ["Contrast dye"],
         ["Is this everything?"], None, 180.0, 243.77)]
