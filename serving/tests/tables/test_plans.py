"""plans.parquet (build/tables/plans.py)."""
import pytest


def test_plans_are_every_plan_on_a_priced_network(corpus):
    assert corpus.build()["plans"] == 3
    assert corpus.rows("plans") == [
        ("45334GA0020002", "Anthem Silver Blue Value HMO 4000", "Anthem Blue Cross and Blue Shield",
         "Silver", False, "45334-GAN001"),
        ("45334GA0020008", "Anthem Bronze Blue Value HMO 5000", "Anthem Blue Cross and Blue Shield",
         "Expanded Bronze", False, "45334-GAN001"),
        ("49046GA0700053", "Anthem Bronze Pathway PCP Copay Choice HMO 6000",
         "Anthem Blue Cross and Blue Shield", "Expanded Bronze", False, "49046-GAN008")]


def test_a_plan_the_index_doesnt_link_to_its_networks_file_stops_the_build(corpus):
    corpus.plan("45334GA0020012", "45334-GAN001", "Anthem Silver Blue Value HMO 6000")
    with pytest.raises(SystemExit, match=r"doesn't link 45334GA0020012 to file 21057, which prices "
                                         r"its network 45334-GAN001"):
        corpus.build()
