"""plans.parquet (build/tables/plans.py)."""


def test_plans_are_every_plan_on_a_priced_network(corpus):
    assert corpus.build()["plans"] == 3
    assert corpus.rows("plans") == [
        ("45334GA0020002", "Anthem Silver Blue Value HMO 4000", "Anthem Blue Cross and Blue Shield",
         "Silver", False, "45334-GAN001", 4000.0),
        ("45334GA0020008", "Anthem Bronze Blue Value HMO 5000", "Anthem Blue Cross and Blue Shield",
         "Expanded Bronze", False, "45334-GAN001", 5000.0),
        ("49046GA0700053", "Anthem Bronze Pathway PCP Copay Choice HMO 6000",
         "Anthem Blue Cross and Blue Shield", "Expanded Bronze", False, "49046-GAN008", 6000.0)]

