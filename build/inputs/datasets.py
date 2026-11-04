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
