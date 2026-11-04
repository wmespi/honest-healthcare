"""The serving tables in the #135 ERD, one file each, named after the table, and
written beside the old ones in build/build.py until those go.

A table's SQL reads the build-only tables in build/inputs/ by name, so
`FROM network_files` is build/inputs/network_files.py. build/table.py holds the
checks and the write every table gets. Nothing here imports from serving/.
"""
import os
import shutil

import duckdb

from build.inputs.biller_places import BillerPlaces
from build.inputs.biller_rates import BillerRates
from build.inputs.hospitals import Hospitals
from build.inputs.imaging_centers import ImagingCenters
from build.inputs.network_files import NetworkFiles
from build.inputs.service_list import SERVICES_PATH, ServiceList
from build.inputs.targets import TARGETS_PATH, Targets
from build.tables.networks import Networks
from build.tables.places import Places
from build.tables.plans import Plans
from build.tables.premiums import Premiums
from build.tables.services import Services
from build.tables.zips import Zips


def inputs(targets_path=TARGETS_PATH, services_path=SERVICES_PATH):
    """The build-only tables, in the order they're made."""
    return [
        Targets(targets_path),
        NetworkFiles(),
        ServiceList(services_path),
        ImagingCenters(),
        BillerRates(),
        Hospitals(),
        BillerPlaces(),
    ]


def build(data_dir, serving_dir, targets_path=TARGETS_PATH, services_path=SERVICES_PATH):
    """Write every serving table under `serving_dir` from the inputs under
    `data_dir` (`data`, or `data-test` for a test build); returns {table: rows}.

    The inputs, then the tables, are made and checked in this order, and each
    one's SQL may read the ones above it. None is written until all of them
    pass, so a failed check leaves the last build's files as they were."""
    tables = [
        Networks(),
        Plans(),
        Premiums(),
        Zips(),
        Services(),
        Places(),
    ]
    spill = os.getenv("DUCKDB_TMP") or f"{serving_dir}/.spill"
    con = connect(spill)
    for table in inputs(targets_path, services_path) + tables:
        table.make(con, data_dir)
    rows = {}
    for table in tables:
        rows[table.name] = table.write(con, serving_dir)
        print(f"  {table.name + '.parquet':<22} {rows[table.name]:>13,}")
    con.close()
    if os.getenv("DUCKDB_TMP") is None:  # only a spill dir we own
        shutil.rmtree(spill, ignore_errors=True)
    return rows


def connect(spill):
    """A DuckDB connection for a build, spilling to `spill`. The old build in
    build/build.py uses it too."""
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET memory_limit = '{os.getenv('DUCKDB_MEMORY_LIMIT', '4GB')}'")
    con.execute(f"SET threads = {os.getenv('DUCKDB_THREADS', '4')}")
    os.makedirs(spill, exist_ok=True)
    con.execute(f"SET temp_directory = '{spill}'")
    con.execute(f"SET max_temp_directory_size = '{os.getenv('DUCKDB_TMP_MAX', '120GiB')}'")
    return con
