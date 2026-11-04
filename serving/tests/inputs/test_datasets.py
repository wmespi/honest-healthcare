"""The datasets the build reads (build/inputs/datasets.py)."""
import os

import pytest


def test_a_missing_dataset_names_the_command_that_makes_it(corpus):
    os.remove(f"{corpus.root}/reference/exchange_networks_ga.parquet")
    with pytest.raises(SystemExit, match="run `make reference STEP=exchange-plans`"):
        corpus.build()
