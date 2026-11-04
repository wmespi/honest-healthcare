"""Build-only: one row per network a target in etl/targets.yaml names, with the
insurer network labels its rates carry."""
import os

import yaml

from build.inputs.datasets import EXCHANGE_NETWORKS, EXCHANGE_PLANS
from build.table import Table

TARGETS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "etl", "targets.yaml")


class Targets(Table):
    """`patterns` are as written, and `likes` are the same patterns as SQL LIKE
    patterns. Every network must be one that Georgia's exchange lists, with at
    least one plan on it."""
    name = "targets"
    key = ["network_id"]
    columns = {"target": "VARCHAR", "network_id": "VARCHAR",
               "patterns": "VARCHAR[]", "likes": "VARCHAR[]"}

    def __init__(self, path):
        self.path = path

    def fill(self, con, data_dir):
        """Made from etl/targets.yaml, not a SELECT; its values go in as parameters."""
        rows = [(name, net, patterns, [_like(p) for p in patterns])
                for name, nets, patterns in read_targets(self.path) for net in nets]
        if not rows:
            raise SystemExit(f"{self.path} names no exchange networks")
        con.execute(f"CREATE TEMP TABLE {self.name} ("
                    + ", ".join(f"{c} {t}" for c, t in self.columns.items()) + ")")
        con.executemany(f"INSERT INTO {self.name} VALUES (?, ?, ?, ?)", rows)

    def check(self, con, data_dir):
        unknown = con.execute(f"""
            SELECT t.target, t.network_id FROM targets t
            WHERE NOT EXISTS (SELECT 1 FROM {EXCHANGE_NETWORKS.read(data_dir)} g
                              WHERE g.network_id = t.network_id)
            ORDER BY t.network_id
        """).fetchone()
        if unknown:
            raise SystemExit(f"{unknown[0]}: {unknown[1]} isn't a network in Georgia's exchange files")
        planless = con.execute(f"""
            SELECT t.target, t.network_id FROM targets t
            WHERE NOT EXISTS (SELECT 1 FROM {EXCHANGE_PLANS.read(data_dir)} p
                              WHERE p.network_id = t.network_id)
            ORDER BY t.network_id
        """).fetchone()
        if planless:
            raise SystemExit(f"{planless[0]}: no plan in Georgia's exchange plan file is on {planless[1]}")


def read_targets(path=TARGETS_PATH):
    """[(name, exchange_networks, network_patterns)] for each target in etl/targets.yaml."""
    with open(path) as f:
        targets = (yaml.safe_load(f) or {}).get("targets") or []
    return [(t.get("name"), t.get("exchange_networks") or [], t.get("network_patterns") or [])
            for t in targets]


def _like(pattern):
    """A target label pattern as a lower-case SQL LIKE pattern with `!` as its
    escape: `*` becomes `%`, and LIKE's own `%` and `_` match only themselves."""
    if pattern is None:
        return None
    escaped = pattern.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return escaped.replace("*", "%")
