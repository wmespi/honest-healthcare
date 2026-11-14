"""cost_sharing.parquet (integration/tables/cost_sharing.py)."""
import pytest

IMAGING = "Imaging (CT/PET Scans, MRIs)"
SILVER = "45334GA0020002"


def shares(corpus):
    """(plan, benefit) -> (copay, coinsurance, deductible first), without the label."""
    corpus.build()
    return {row[:2]: row[3:] for row in corpus.rows("cost_sharing")}


def test_each_plan_has_its_share_for_each_category_the_services_use(corpus):
    assert shares(corpus) == {
        (plan, category): share
        for plan in ("45334GA0020002", "45334GA0020008", "49046GA0700053")
        for category, share in [("advanced_imaging", (None, 40.0, True)), ("preventive", (0.0, None, False)),
                                ("primary_care", (30.0, None, False)), ("specialist", (60.0, None, False))]}


def test_each_row_is_by_benefit_with_the_label_the_screen_shows(corpus):
    corpus.build()
    assert {r[1]: r[2] for r in corpus.rows("cost_sharing") if r[0] == SILVER} == {
        "advanced_imaging": "CT, PET and MRI scans", "preventive": "Preventive care",
        "primary_care": "Primary care visit", "specialist": "Specialist visit"}


@pytest.mark.parametrize("copay, coinsurance, share", [
    ("$300.00 Copay after deductible", "50.00% Coinsurance after deductible", (300.0, 50.0, True)),
    ("$1,250.00", "Not Applicable", (1250.0, None, False)),
    ("No Charge after deductible", "Not Applicable", (0.0, None, True)),
    ("Not Applicable", "20.00%", (None, 20.0, False)),
])
def test_georgias_wording_becomes_a_copay_a_coinsurance_and_whether_the_deductible_comes_first(
        corpus, copay, coinsurance, share):
    corpus.cost_sharing(SILVER, IMAGING, copay, coinsurance)
    assert shares(corpus)[(SILVER, "advanced_imaging")] == share


@pytest.mark.parametrize("copay, coinsurance, covered, why", [
    (None, None, False, "doesn't cover"),
    ("Not Applicable", "Not Applicable", True, "lists no copay or coinsurance for"),
    ("$300.00 Copay per Day", "Not Applicable", True, "words its share in a way the build doesn't read"),
    ("$65.00", "40.00% Coinsurance after deductible", True, "applies the deductible to only one"),
])
def test_a_share_the_screen_cant_show_stops_the_build(corpus, copay, coinsurance, covered, why):
    corpus.cost_sharing(SILVER, IMAGING, copay, coinsurance, covered=covered)
    with pytest.raises(SystemExit, match=f"cost_sharing: {SILVER} {why}"):
        corpus.build()


def test_a_category_georgias_file_has_no_row_for_stops_the_build(corpus):
    corpus.families["benefits"]["diagnostic_imaging"] = {"name": "X-rays and Diagnostic Imaging", "label": "X-rays"}
    corpus.write_families()
    corpus.service("xray-chest", "71046", benefits=["diagnostic_imaging"])
    with pytest.raises(SystemExit, match="has no row in Georgia's cost-sharing file for 'X-rays and Diagnostic"):
        corpus.build()
