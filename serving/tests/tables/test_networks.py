"""networks.parquet (build/tables/networks.py)."""


def test_networks_are_the_targets_named_by_georgia(corpus):
    assert corpus.build() == {"networks": 2}
    assert corpus.rows("networks") == [
        ("45334-GAN001", "Blue Value Individual Network HMO"),
        ("49046-GAN008", "Pathway PCP Copay Choice")]
