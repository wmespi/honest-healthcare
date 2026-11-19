"""plans.parquet (integration/tables/plans.py)."""
import pytest

SILVER = "Anthem Silver Blue Value HMO 4000"


def test_plans_are_every_plan_on_a_priced_network(corpus):
    assert corpus.build()["plans"] == 3
    assert corpus.rows("plans") == [
        ("45334GA0020002", "Anthem Silver Blue Value HMO 4000", "Anthem Blue Cross and Blue Shield",
         "Silver", False, "45334-GAN001", 4000.0, ["45334GA0020002"]),
        ("45334GA0020008", "Anthem Bronze Blue Value HMO 5000", "Anthem Blue Cross and Blue Shield",
         "Expanded Bronze", False, "45334-GAN001", 5000.0, ["45334GA0020008"]),
        ("49046GA0700053", "Anthem Bronze Pathway PCP Copay Choice HMO 6000",
         "Anthem Blue Cross and Blue Shield", "Expanded Bronze", False, "49046-GAN008", 6000.0, ["49046GA0700053"])]



def test_one_design_sold_in_two_service_areas_is_one_plan(corpus):
    corpus.plan("45334GA0020001", "45334-GAN001", SILVER)
    corpus.premium("45334GA0020001", 1)
    assert corpus.build()["plans"] == 3
    row = [r for r in corpus.rows("plans") if r[1] == SILVER][0]
    assert row[0] == "45334GA0020001"
    assert row[7] == ["45334GA0020001", "45334GA0020002"]
    assert {r[0] for r in corpus.rows("premiums")} >= {"45334GA0020001", "45334GA0020002"}


def test_an_insurer_doing_business_under_another_name_shows_that_name(corpus):
    corpus.plan("45334GA0020009", "45334-GAN001", "Clear Silver HMO", carrier="Centene Corporation DBA Ambetter "
                "from Peach State Health Plan")
    corpus.premium("45334GA0020009", 1)
    corpus.build()
    assert {r[2] for r in corpus.rows("plans")} == {"Anthem Blue Cross and Blue Shield",
                                                     "Ambetter from Peach State Health Plan"}


def test_an_insurer_shows_its_name_without_its_company_form(corpus):
    for i, carrier in enumerate(["Alliant Health Plans, Inc.", "CareSource Georgia Co."]):
        corpus.plan(f"45334GA002001{i}", "45334-GAN001", f"Plan {i}", carrier=carrier)
        corpus.premium(f"45334GA002001{i}", 1)
    corpus.build()
    assert {r[2] for r in corpus.rows("plans")} == {"Anthem Blue Cross and Blue Shield", "Alliant Health Plans",
                                                     "CareSource Georgia"}


def test_the_same_name_on_another_network_is_its_own_plan(corpus):
    corpus.plan("49046GA0700001", "49046-GAN008", SILVER)
    corpus.premium("49046GA0700001", 1)
    assert corpus.build()["plans"] == 4


def test_a_design_whose_plan_ids_differ_in_cost_sharing_stops_the_build(corpus):
    corpus.plan("45334GA0020001", "45334-GAN001", SILVER)
    corpus.premium("45334GA0020001", 1)
    corpus.cost_sharing("45334GA0020001", "Specialist Visit", "$75.00 Copay", "Not Applicable")
    with pytest.raises(SystemExit, match=r"plans: .*Silver Blue Value HMO 4000.* differ .*'Specialist Visit'"):
        corpus.build()
