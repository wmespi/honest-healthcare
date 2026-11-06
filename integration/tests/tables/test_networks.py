"""networks.parquet (integration/tables/networks.py)."""
import pytest


def test_networks_are_the_ones_the_payer_rates_price_named_by_georgia(corpus):
    assert corpus.build()["networks"] == 2
    assert corpus.rows("networks") == [
        ("45334-GAN001", "Blue Value Individual Network HMO"),
        ("49046-GAN008", "Pathway PCP Copay Choice")]


def test_rates_on_a_network_georgia_doesnt_list_stop_the_build(corpus):
    corpus.rate("45334-GAN009")
    with pytest.raises(SystemExit, match="the payer rates price 45334-GAN009, which isn't a network"):
        corpus.build()
