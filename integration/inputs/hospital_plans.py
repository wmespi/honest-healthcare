"""Build-only: what each payer and plan name in a hospital's own price file means,
from the reviewed list in integration/hospital_plans.yaml."""
import os

import yaml

from integration.inputs.datasets import HOSPITAL_RATES, PAYER_NETWORKS
from integration.table import Table

HOSPITAL_PLANS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "hospital_plans.yaml")


def plan_name(r):
    """SQL for the name a row of a hospital's own file prices under, "<payer> |
    <plan>" as the file gives them: "<payer> |" when it gives no plan, and empty
    on a row that lists only the hospital's charges."""
    return (f"CASE WHEN {r}.payer IS NOT NULL OR {r}.plan IS NOT NULL "
            f"THEN trim(coalesce({r}.payer, '') || ' | ' || coalesce({r}.plan, '')) END")


class HospitalPlans(Table):
    """One row per hospital (`ccn`) and name its file uses, with the exchange
    network the name means (`network_id`), or none when someone read it and
    found it isn't a plan on a network we price.

    A hospital's file names each rate's payer and plan in its own words, so a
    rate there prices a hospital's own fee only under a name someone has read.
    The build stops on a name a hospital's file has that the list doesn't give
    for that hospital, so a new name is read before it is priced or left out,
    on a hospital listed twice or a name listed twice for one hospital, and on a
    network the payer rates don't list."""
    name = "hospital_plans"
    key = ["ccn", "name"]
    columns = {"ccn": "VARCHAR", "name": "VARCHAR", "network_id": "VARCHAR"}

    def __init__(self, path):
        self.path = path

    def fill(self, con, data_dir):
        """Made from integration/hospital_plans.yaml, not a SELECT; its values go in as parameters."""
        rows, group_of = {}, {}
        for i, group in enumerate(read_hospital_plans(self.path), 1):
            unknown = sorted(set(group) - {"hospitals", "networks", "other"})
            if unknown:
                raise SystemExit(f"{self.path}: group {i} has fields the build doesn't read: {unknown}")
            if not group.get("hospitals"):
                raise SystemExit(f"{self.path}: group {i} lists no hospitals")
            if not isinstance(group.get("networks") or {}, dict):
                raise SystemExit(f"{self.path}: group {i}'s networks aren't each a network ID with its names")
            names = [(n, network) for network, ns in (group.get("networks") or {}).items() for n in ns or []]
            names += [(n, None) for n in group.get("other") or []]
            for ccn in map(str, group["hospitals"]):
                if ccn in group_of:
                    raise SystemExit(f"{self.path}: hospital {ccn} is in group {group_of[ccn]} and group {i}")
                group_of[ccn] = i
                for name, network in names:
                    name = str(name).strip()
                    if (ccn, name) in rows:
                        raise SystemExit(f"{self.path}: group {i} lists {name!r} twice for hospital {ccn}")
                    rows[(ccn, name)] = network
        con.execute(f"CREATE TEMP TABLE {self.name} ("
                    + ", ".join(f"{c} {t}" for c, t in self.columns.items()) + ")")
        if rows:
            con.executemany(f"INSERT INTO {self.name} VALUES (?, ?, ?)",
                            [[ccn, name, network] for (ccn, name), network in rows.items()])

    def check(self, con, data_dir):
        stray = con.execute(f"""
            SELECT DISTINCT network_id FROM hospital_plans
            WHERE network_id NOT IN (SELECT network_id FROM {PAYER_NETWORKS.read(data_dir)})
            ORDER BY network_id
        """).fetchone()
        if stray:
            raise SystemExit(f"{self.path}: {stray[0]} isn't a network the payer rates list")
        unknown = [f"{ccn} {name!r}" for ccn, name in con.execute(f"""
            SELECT DISTINCT r.ccn, {plan_name("r")} AS name
            FROM {HOSPITAL_RATES.read(data_dir)} r
            WHERE {plan_name("r")} IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM hospital_plans p WHERE p.ccn = r.ccn AND p.name = {plan_name("r")})
            ORDER BY r.ccn, name
        """).fetchall()]
        if unknown:
            raise SystemExit(f"hospital_plans: hospitals' own price files use names {self.path} doesn't give for "
                             f"them ({len(unknown)}): {', '.join(unknown)}; list each under `networks` or "
                             f"`other` in its hospital's group")


def read_hospital_plans(path=HOSPITAL_PLANS_PATH):
    """Each group in integration/hospital_plans.yaml, as a dict of its fields."""
    with open(path) as f:
        groups = yaml.safe_load(f) or []
    if not isinstance(groups, list) or not all(isinstance(g, dict) for g in groups):
        raise SystemExit(f"{path} isn't a list of groups, each with `hospitals`, `networks` and `other`")
    return groups
