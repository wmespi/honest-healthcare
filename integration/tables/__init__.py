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

from integration.inputs.addresses import Addresses
from integration.inputs.biller_doctors import BillerDoctors
from integration.inputs.biller_places import BillerPlaces
from integration.inputs.biller_rates import BillerRates
from integration.inputs.datasets import check_inputs
from integration.inputs.families import FAMILIES_PATH, Benefits, Centers, FamilyBills, FamilyList, Kinds
from integration.inputs.hospital_cash import HospitalCash
from integration.inputs.hospital_fees import FacilityRates, HospitalFees
from integration.inputs.hospital_plans import HOSPITAL_PLANS_PATH, HospitalPlans
from integration.inputs.hospitals import Hospitals
from integration.inputs.service_list import SERVICES_PATH, ServiceList
from integration.inputs.service_sites import ServiceSites
from integration.tables.cost_sharing import CostSharing
from integration.tables.families import Families
from integration.tables.networks import Networks
from integration.tables.places import Places, UnlocatedPlaces
from integration.tables.prices import Prices
from integration.tables.plans import Plans
from integration.tables.premiums import Premiums
from integration.tables.providers import Providers
from integration.tables.services import Services
from integration.tables.zips import Zips


def inputs(services_path=SERVICES_PATH, hospital_plans_path=HOSPITAL_PLANS_PATH, families_path=FAMILIES_PATH):
    """The build-only tables, in the order they're made."""
    return [
        Benefits(families_path),
        Kinds(families_path),
        FamilyList(families_path),
        FamilyBills(families_path),
        Centers(families_path),
        ServiceList(services_path, families_path),
        HospitalPlans(hospital_plans_path),
        BillerDoctors(),
        BillerRates(),
        FacilityRates(),
        Addresses(),
        Hospitals(),
        BillerPlaces(),
        ServiceSites(),
        HospitalFees(),
        HospitalCash(),
    ]


def serving():
    """The serving tables, in the order they're made."""
    return [
        Networks(),
        Plans(),
        Premiums(),
        Zips(),
        CostSharing(),
        Places(),
        UnlocatedPlaces(),
        Prices(),
        Services(),
        Families(),
        Providers(),
    ]


def make(con, data_dir, services_path=SERVICES_PATH, hospital_plans_path=HOSPITAL_PLANS_PATH,
         families_path=FAMILIES_PATH):
    """Make every input and serving table in `con` from the inputs under
    `data_dir`, writing nothing; returns the serving tables.

    Every dataset is checked first (integration/inputs/datasets.py), then the inputs
    and the tables are made and checked in this order, and each one's SQL may
    read the ones above it."""
    tables = serving()
    check_inputs(con, data_dir)
    for table in inputs(services_path, hospital_plans_path, families_path) + tables:
        table.make(con, data_dir)
    return tables


def build(data_dir, serving_dir, services_path=SERVICES_PATH, hospital_plans_path=HOSPITAL_PLANS_PATH,
          families_path=FAMILIES_PATH):
    """Write every serving table under `serving_dir` from the inputs under
    `data_dir` (`data`, or `data-test` for a test build); returns {table: rows}.

    None is written until all of them are made and pass their checks (`make`),
    and none is moved into place until all of them are written, so a failed
    check or a failed write leaves the last build's files as they were. The app
    reads the files as they are, so it can see two builds' tables side by side
    only in the moment the renames take, not while the tables are written."""
    spill = os.getenv("DUCKDB_TMP") or f"{serving_dir}/.spill"
    con = connect(spill)
    try:
        tables = make(con, data_dir, services_path, hospital_plans_path, families_path)
        rows = {}
        try:
            for table in tables:
                rows[table.name] = table.stage(con, serving_dir)
        except BaseException:
            for table in tables:
                if os.path.exists(table.staged_path(serving_dir)):
                    os.remove(table.staged_path(serving_dir))
            raise
        for table in tables:
            os.replace(table.staged_path(serving_dir), table.path(serving_dir))
        width = max(len(f"{table.name}.parquet") for table in tables)
        for table in tables:
            print(f"  {table.name + '.parquet':<{width}} {rows[table.name]:>13,}")
    finally:
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
