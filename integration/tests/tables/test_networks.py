"""networks.parquet (integration/tables/networks.py)."""
import datetime

import pytest

from integration.tests.corpus import BLUE_VALUE, PATHWAY

OCT31 = datetime.date(2026, 10, 31)
FAILED = "the latest parse of GA_FILE.json.gz, on 2026-11-07 18:14, failed: malformed MRF"


def test_networks_are_the_ones_the_payer_rates_list_named_by_georgia(corpus):
    assert corpus.build()["networks"] == 2
    assert corpus.rows("networks") == [
        (BLUE_VALUE, "Blue Value Individual Network HMO", "current", OCT31, None),
        (PATHWAY, "Pathway PCP Copay Choice", "current", OCT31, None)]


def test_a_network_whose_latest_copy_failed_keeps_its_prices_and_plans_and_says_so(corpus):
    corpus.network(PATHWAY, "stale", reason=FAILED)
    corpus.build()
    assert corpus.rows("networks")[1] == (PATHWAY, "Pathway PCP Copay Choice", "stale", OCT31, FAILED)
    assert "49046GA0700053" in [p[0] for p in corpus.rows("plans")]


def test_a_network_with_no_prices_is_listed_but_offers_no_plan(corpus):
    corpus.remove_rates(PATHWAY)
    corpus.network(PATHWAY, "missing", reason=FAILED)
    assert corpus.build()["networks"] == 2
    assert corpus.rows("networks")[1] == (PATHWAY, "Pathway PCP Copay Choice", "missing", None, FAILED)
    assert {p[5] for p in corpus.rows("plans")} == {BLUE_VALUE}


def test_rates_on_a_network_georgia_doesnt_list_stop_the_build(corpus):
    corpus.rate("45334-GAN009")
    with pytest.raises(SystemExit, match="the payer rates list 45334-GAN009, which isn't a network"):
        corpus.build()


def test_rates_on_a_network_they_say_has_none_stop_the_build(corpus):
    corpus.network(PATHWAY, "missing", reason=FAILED)
    with pytest.raises(SystemExit, match=f"the payer rates price {PATHWAY}, but say it has none"):
        corpus.build()


def test_current_prices_with_no_rates_stop_the_build(corpus):
    corpus.remove_rates(PATHWAY)
    corpus.network(PATHWAY, "stale", reason=FAILED)
    with pytest.raises(SystemExit, match=f"the payer rates say {PATHWAY}'s prices are stale, but have none for it"):
        corpus.build()


def test_a_status_the_build_doesnt_know_stops_it(corpus):
    corpus.network(PATHWAY, "fresh")
    with pytest.raises(SystemExit, match=f"{PATHWAY}'s prices are 'fresh', not one of current, stale, missing"):
        corpus.build()
