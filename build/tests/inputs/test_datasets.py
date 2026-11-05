"""The datasets the build reads (build/inputs/datasets.py): the contract with
ingestion, checked before the build reads any of them."""
import os

import duckdb
import pytest


def test_a_missing_dataset_names_the_command_that_makes_it(corpus):
    os.remove(f"{corpus.root}/reference/exchange_networks_ga.parquet")
    with pytest.raises(SystemExit, match="run `make reference STEP=exchange-plans`"):
        corpus.build()


def test_missing_payer_rates_name_parse(corpus):
    os.remove(f"{corpus.root}/payer_rates/anthem/rates.parquet")
    with pytest.raises(SystemExit, match="run `make parse`"):
        corpus.build()


def rewrite(path, select):
    """Replace a dataset's file with a SELECT over it (`t`)."""
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS FROM '{path}'")
    con.execute(f"COPY ({select}) TO '{path}' (FORMAT parquet)")


def test_a_dataset_without_a_column_the_build_reads_stops_it(corpus):
    corpus.flush()
    rewrite(f"{corpus.root}/payer_rates/anthem/rosters.parquet", "SELECT * EXCLUDE (tin_type) FROM t")
    with pytest.raises(SystemExit, match="rosters.parquet: column tin_type is missing"):
        corpus.build()


def test_a_column_of_another_type_stops_the_build(corpus):
    corpus.flush()
    rewrite(f"{corpus.root}/nppes/ga_providers.parquet", "SELECT * REPLACE (npi::VARCHAR AS npi) FROM t")
    with pytest.raises(SystemExit, match="ga_providers.parquet: column npi is VARCHAR, but the build reads it as BIGINT"):
        corpus.build()


def test_a_repeated_key_stops_the_build(corpus):
    corpus.flush()
    rewrite(f"{corpus.root}/reference/hospitals_ga.parquet", "FROM t UNION ALL FROM t")
    with pytest.raises(SystemExit, match=r"hospitals_ga.parquet: the key \(ccn\) repeats"):
        corpus.build()


def test_extra_columns_are_allowed(corpus):
    corpus.flush()
    rewrite(f"{corpus.root}/reference/exchange_plans_ga.parquet", "SELECT *, 2026 AS plan_year FROM t")
    assert corpus.build()["plans"] == 3
