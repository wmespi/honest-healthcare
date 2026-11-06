"""The serving tables in the #135 ERD, one file each, named after the table.
unlocated_places, the places left off the map, is written beside them, and
nothing serves it.

A table's SQL reads the build-only tables in integration/inputs/ by name, so
`FROM service_list` is integration/inputs/service_list.py, and reads the datasets
ingestion writes through integration/inputs/datasets.py, which checks them first.
integration/table.py holds the checks and the write every table gets. Nothing here
imports from application/serving/ or ingestion/.
"""
import os
import shutil

import duckdb

from integration.inputs.biller_doctors import BillerDoctors
from integration.inputs.biller_places import BillerPlaces
from integration.inputs.biller_rates import BillerRates
from integration.inputs.datasets import check_inputs
from integration.inputs.hospitals import Hospitals
from integration.inputs.imaging_centers import ImagingCenters
from integration.inputs.service_list import SERVICES_PATH, ServiceList
from integration.tables.cost_sharing import CostSharing
from integration.tables.networks import Networks
from integration.tables.places import Places, UnlocatedPlaces
from integration.tables.prices import Prices
from integration.tables.plans import Plans
from integration.tables.premiums import Premiums
from integration.tables.providers import Providers
from integration.tables.services import Services
from integration.tables.zips import Zips


def inputs(services_path=SERVICES_PATH):
    """The build-only tables, in the order they're made."""
    return [
        ServiceList(services_path),
        ImagingCenters(),
        BillerDoctors(),
        BillerRates(),
        Hospitals(),
        BillerPlaces(),
    ]


def build(data_dir, serving_dir, services_path=SERVICES_PATH):
    """Write every serving table under `serving_dir` from the inputs under
    `data_dir` (`data`, or `data-test` for a test build); returns {table: rows}.

    Every dataset is checked first (integration/inputs/datasets.py), then the inputs
    and the tables are made and checked in this order, and each one's SQL may
    read the ones above it. None is written until all of them
    pass, so a failed check leaves the last build's files as they were."""
    tables = [
        Networks(),
        Plans(),
        Premiums(),
        Zips(),
        Services(),
        CostSharing(),
        Places(),
        UnlocatedPlaces(),
        Prices(),
        Providers(),
    ]
    spill = os.getenv("DUCKDB_TMP") or f"{serving_dir}/.spill"
    con = connect(spill)
    check_inputs(con, data_dir)
    for table in inputs(services_path) + tables:
        table.make(con, data_dir)
    rows = {}
    for table in tables:
        rows[table.name] = table.write(con, serving_dir)
        print(f"  {table.name + '.parquet':<22} {rows[table.name]:>13,}")
    con.close()
    if not os.getenv("DUCKDB_TMP"):  # only a spill dir we own (make build passes it empty)
        shutil.rmtree(spill, ignore_errors=True)
    return rows


def connect(spill):
    """A DuckDB connection for a build, spilling to `spill`."""
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET memory_limit = '{os.getenv('DUCKDB_MEMORY_LIMIT', '4GB')}'")
    con.execute(f"SET threads = {os.getenv('DUCKDB_THREADS', '4')}")
    os.makedirs(spill, exist_ok=True)
    con.execute(f"SET temp_directory = '{spill}'")
    con.execute(f"SET max_temp_directory_size = '{os.getenv('DUCKDB_TMP_MAX', '120GiB')}'")
    return con
