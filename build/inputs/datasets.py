"""The datasets the build reads, each with the command that makes it."""
import glob


class Dataset:
    """Parquet files under the data directory, and the command that makes them."""

    def __init__(self, path, made_by, options=""):
        self.path, self.made_by, self.options = path, made_by, options

    def read(self, data_dir):
        """A DuckDB table expression for the dataset, or stop the build with the
        command that makes it."""
        path = f"{data_dir}/{self.path}"
        if not glob.glob(path):
            raise SystemExit(f"no {path}: run `{self.made_by}`")
        return f"read_parquet('{path}'{self.options})"


# Every rate `make parse` has written from the insurers' files, one folder per network label.
INSURER_RATES = Dataset("anthem/prices/*/*.parquet", "make parse",
                        ", hive_partitioning=1, union_by_name=true")
# Who each price covers: a price's group set lists provider groups, and each group
# lists its doctors (`npi`) and its billing NPI (`tin_value`) (docs/schema.md).
GROUP_SETS = Dataset("anthem/group_sets/*.parquet", "make parse")
ROSTERS = Dataset("anthem/providers/*.parquet", "make parse")
# The plan -> file link in the index `make discover` last read whole: file_id,
# plan_id (etl/discover.md).
INDEX_LINKS = Dataset("anthem/index_file_plans.parquet", "make discover")
# Georgia's exchange networks: network_id, issuer_id, name (reference/exchange-plans.md).
EXCHANGE_NETWORKS = Dataset("reference/exchange_networks_ga.parquet",
                            "make reference STEP=exchange-plans")
# Georgia's exchange plans, each with its network_id (reference/exchange-plans.md).
EXCHANGE_PLANS = Dataset("reference/exchange_plans_ga.parquet",
                         "make reference STEP=exchange-plans")
# Each plan's monthly premium by rating area and age (reference/exchange-plans.md).
EXCHANGE_PREMIUMS = Dataset("reference/exchange_premiums_ga.parquet",
                            "make reference STEP=exchange-plans")
# Medicare's physician fee schedule in Georgia, by code, modifier, place of
# service and payment area (reference/mpfs.md).
MPFS = Dataset("reference/mpfs_ga.parquet", "make reference STEP=mpfs")
# Medicare's national hospital outpatient rate by code (reference/opps.md).
OPPS = Dataset("reference/opps.parquet", "make reference STEP=opps")
# Each Georgia NPI's own record: name, specialty code and practice address (etl/nppes.md).
NPPES = Dataset("nppes/ga_providers.parquet", "make reference STEP=nppes")
# Who billed Medicare for each code in Georgia, and whether in an office (O) or a
# facility (F) (reference/cms-utilization.md).
MEDICARE_BILLING = Dataset("cms/ga_provider_service.parquet", "make reference STEP=cms-utilization")
# Each clinician's CMS group, by NPI (reference/doctors-clinicians.md).
DOCTORS_CLINICIANS = Dataset("reference/dac_ga.parquet", "make reference STEP=doctors-clinicians")
# Georgia's hospitals: CCN, name, address, phone and star rating (reference/hospitals.md).
HOSPITALS = Dataset("reference/hospitals_ga.parquet", "make reference STEP=hospitals")
# Every address sent to Census, with its standardized match and coordinates
# (reference/geocode.md).
GEOCODES = Dataset("reference/geocode_addresses.parquet", "make reference STEP=geocode")
# Each ZIP's Census centroid: zip, lat, lon (reference/zip-centroids.md).
ZIP_CENTROIDS = Dataset("reference/zip_centroids.parquet", "make reference STEP=zip-centroids")
