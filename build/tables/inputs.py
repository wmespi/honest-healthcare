"""The datasets the tables read, each with the command that makes it, and the
targets file that names the plans the build covers."""
import glob
import os

import yaml

TARGETS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "etl", "targets.yaml")


class Input:
    """A dataset the tables read: its Parquet files under the data directory,
    and the command that makes them."""

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
INSURER_RATES = Input("anthem/prices/*/*.parquet", "make parse",
                      ", hive_partitioning=1, union_by_name=true")
# Georgia's exchange networks: network_id, issuer_id, name (reference/exchange-plans.md).
EXCHANGE_NETWORKS = Input("reference/exchange_networks_ga.parquet",
                          "make reference STEP=exchange-plans")


def read_targets(path=TARGETS_PATH):
    """[(name, exchange_networks, network_patterns)] for each target in etl/targets.yaml."""
    with open(path) as f:
        targets = (yaml.safe_load(f) or {}).get("targets") or []
    return [(t.get("name"), t.get("exchange_networks") or [], t.get("network_patterns") or [])
            for t in targets]
