"""The `anthem` fixture: Anthem's files as parse and discover write them
(data/anthem/), Georgia's exchange networks and plans, and a targets file, in a
tmp dir, for ingestion/anthem_rates.py to publish."""
import os

import duckdb
import pytest

BLUE_VALUE_LABEL = "GA Blue Value HIX Individual Network"
TARGETS = """targets:
  - name: Blue Value
    exchange_networks: ["45334-GAN001"]
    network_patterns: ["GA Blue Value HIX*"]
  - name: Pathway
    exchange_networks: ["49046-GAN008"]
    network_patterns: ["Pathway PCP Copay Choice*"]
"""


def _write(path, columns, rows):
    """Write `rows` of SQL literals to `path` as Parquet with `columns` (name -> type)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = duckdb.connect()
    con.execute("CREATE TABLE t (" + ", ".join(f"{c} {t}" for c, t in columns.items()) + ")")
    if rows:
        con.execute("INSERT INTO t VALUES " + ", ".join(
            "(" + ", ".join("NULL" if v is None else repr(v) if not isinstance(v, str) else
                            "'" + v.replace("'", "''") + "'" for v in r) + ")" for r in rows))
    con.execute(f"COPY t TO '{path}' (FORMAT parquet)")


class Anthem:
    """Raw rates, rate groups and rosters by file, the index's plan links,
    Georgia's networks and plans, and the targets file."""

    def __init__(self, root):
        self.root = root
        self.targets = f"{root}/targets.yaml"
        self.out = f"{root}/payer_rates/anthem"
        self.rates = {}   # (file_id, label) -> rows
        self.groups = {}  # file_id -> rows
        self.rosters = {}  # file_id -> rows
        self.links = []
        self.plans = []
        with open(self.targets, "w") as f:
            f.write(TARGETS)
        _write(f"{root}/reference/exchange_networks_ga.parquet",
               {"network_id": "VARCHAR", "issuer_id": "VARCHAR", "name": "VARCHAR"},
               [("45334-GAN001", "45334", "Blue Value"), ("49046-GAN008", "49046", "Pathway"),
                ("58081-GAN001", "58081", "Individual Georgia HMO")])

    def rate(self, file_id, label, group_set=1, code="70551", rate=250.0):
        """Add a rate in file `file_id` under network label `label`."""
        rows = self.rates.setdefault((file_id, label), [])
        rows.append((file_id, group_set, label, "CPT", code, "ffs", "negotiated", rate, "9999-12-31", "11",
                     "professional", "", "outpatient"))
        self._write_rates(file_id, label)

    def remove_rate(self, file_id, label):
        del self.rates[(file_id, label)]
        os.remove(self._rates_path(file_id, label))

    def _rates_path(self, file_id, label):
        slug = "".join(ch if ch.isalnum() else "-" for ch in label.strip().lower())
        return f"{self.root}/anthem/prices/net={slug}/{file_id}.parquet"

    def _write_rates(self, file_id, label):
        _write(self._rates_path(file_id, label), {
            "file_id": "BIGINT", "group_set_id": "BIGINT", "network_name": "VARCHAR",
            "billing_code_type": "VARCHAR", "billing_code": "VARCHAR", "negotiation_arrangement": "VARCHAR",
            "negotiated_type": "VARCHAR", "negotiated_rate": "DOUBLE", "expiration_date": "VARCHAR",
            "service_code": "VARCHAR", "billing_class": "VARCHAR", "modifier": "VARCHAR", "setting": "VARCHAR"},
            self.rates[(file_id, label)])

    def group(self, file_id, group_set, group, tin, *npis, label="any network"):
        """Put provider group `group`, billing as `tin`, with doctors `npis`, in
        `group_set` of file `file_id`. A group's label can list several networks."""
        self.groups.setdefault(file_id, []).append((file_id, group_set, group))
        self.rosters.setdefault(file_id, []).extend((file_id, group, label, npi, "npi", tin) for npi in npis)
        _write(f"{self.root}/anthem/group_sets/{file_id}.parquet",
               {"file_id": "BIGINT", "group_set_id": "BIGINT", "provider_group_id": "BIGINT"},
               self.groups[file_id])
        _write(f"{self.root}/anthem/providers/{file_id}.parquet",
               {"file_id": "BIGINT", "provider_group_id": "BIGINT", "network_name": "VARCHAR", "npi": "BIGINT",
                "tin_type": "VARCHAR", "tin_value": "VARCHAR"}, self.rosters[file_id])

    def link(self, file_id, *plan_ids):
        """Have the index link file `file_id` to each of `plan_ids`."""
        self.links += [(file_id, p) for p in plan_ids]
        self._write_links()

    def unlink(self, file_id):
        self.links = [(f, p) for f, p in self.links if f != file_id]
        self._write_links()

    def _write_links(self):
        _write(f"{self.root}/anthem/index_file_plans.parquet", {"file_id": "BIGINT", "plan_id": "VARCHAR"},
               self.links)

    def plan(self, plan_id, network_id):
        """Add a plan on `network_id` to Georgia's plan file."""
        self.plans.append((plan_id, network_id))
        self._write_plans()

    def remove_plans(self, network_id):
        self.plans = [p for p in self.plans if p[1] != network_id]
        self._write_plans()

    def _write_plans(self):
        _write(f"{self.root}/reference/exchange_plans_ga.parquet", {"plan_id": "VARCHAR", "network_id": "VARCHAR"},
               self.plans)

    def add_target(self, text):
        """Add a target to the targets file, as YAML list items."""
        with open(self.targets, "a") as f:
            f.write(text)

    def publish(self):
        from ingestion.anthem_rates import publish
        return publish(self.root, self.out, self.targets)

    def rows(self, name):
        return duckdb.connect().execute(f"FROM '{self.out}/{name}.parquet' ORDER BY ALL").fetchall()


@pytest.fixture()
def anthem(tmp_path, monkeypatch):
    """Blue Value in file 21057 and Pathway beside an untargeted network in file
    701, each linked to its network's plans, and one priced group in each."""
    monkeypatch.delenv("DUCKDB_TMP", raising=False)
    a = Anthem(str(tmp_path))
    for plan_id, net in [("45334GA0020002", "45334-GAN001"), ("45334GA0020008", "45334-GAN001"),
                         ("49046GA0700053", "49046-GAN008"), ("58081GA0010001", "58081-GAN001")]:
        a.plan(plan_id, net)
    a.rate(21057, BLUE_VALUE_LABEL)
    a.group(21057, 1, 10, "1000000001", 2000000001, 2000000002, label=BLUE_VALUE_LABEL)
    a.rate(701, "Pathway PCP Copay Choice", group_set=5)
    a.rate(701, "TRADITIONAL HEALTH PLAN", group_set=6)
    a.group(701, 5, 50, "1000000005", 2000000005, label="Pathway PCP Copay Choice|TRADITIONAL HEALTH PLAN")
    a.group(701, 6, 60, "1000000006", 2000000006, label="TRADITIONAL HEALTH PLAN")
    a.link(21057, "45334GA0020002", "45334GA0020008")
    a.link(701, "49046GA0700053")
    return a
