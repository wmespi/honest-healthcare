"""The serving tables the find-care screen reads (#135), written beside the old
ones in build/build.py until those go.

Each table has its own file, named after it, so `FROM network_files` in any
table's SQL is the table in network_files.py. `table.py` holds what every table
gets (its checks and its write), and `inputs.py` the datasets the tables read.
Nothing here imports from serving/.
"""
import os
import shutil

import duckdb

from build.tables.inputs import TARGETS_PATH
from build.tables.network_files import NetworkFiles
from build.tables.networks import Networks
from build.tables.targets import Targets


def build(data_dir, serving_dir, targets_path=TARGETS_PATH):
    """Write every table under `serving_dir` from the inputs under `data_dir`
    (`data`, or `data-test` for a test build); returns {table: rows}.

    The tables are made and checked in this order, and a table's SQL may read
    the ones above it. None is written until all of them pass, so a failed
    check leaves the last build's files as they were."""
    tables = [
        Targets(targets_path),
        NetworkFiles(),
        Networks(),
    ]
    spill = os.getenv("DUCKDB_TMP") or f"{serving_dir}/.spill"
    con = connect(spill)
    for table in tables:
        table.make(con, data_dir)
    rows = {}
    for table in tables:
        if table.written:
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
