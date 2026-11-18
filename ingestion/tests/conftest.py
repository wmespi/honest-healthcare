"""The `anthem` and `ambetter` fixtures: a payer's files as parse and discover write
them (data/raw/<payer>/), Georgia's exchange networks and plans, and a targets
file naming both payers, in a tmp dir, for ingestion/payer_rates.py to publish."""
import os

import duckdb
import pytest

BLUE_VALUE_LABEL = "GA Blue Value HIX Individual Network"
AMBETTER_FILE = "2026-10-27_centene_ambetter-ga_in-network.json"
TARGETS = """targets:
  - name: Ambetter Premier
    payer: ambetter
    exchange_networks: ["70893-GAN001"]
    files: ["*_ambetter-ga_in-network.json"]
    network_patterns: ["Exchange GA"]
  - name: Ambetter Select Plus
    payer: ambetter
    exchange_networks: ["70893-GAN003"]
    files: ["*_ambetter-ga_in-network.json"]
    network_patterns: ["Exchange Select Plus"]
  - name: Blue Value
    payer: anthem
    exchange_networks: ["45334-GAN001"]
    files: ["GA_BV.json.gz"]
    network_patterns: ["GA Blue Value HIX*"]
  - name: Pathway
    payer: anthem
    exchange_networks: ["49046-GAN008"]
    files: ["GA_PW.json.gz"]
    network_patterns: ["Pathway PCP Copay Choice*"]
"""
# When the payer last updated each file the fixture's parse read.
OCT31 = "2026-10-31 09:24:31+00"
_RECORD = {"file_id": "BIGINT", "name": "VARCHAR", "address": "VARCHAR", "status": "VARCHAR", "reason": "VARCHAR",
           "updated_at": "TIMESTAMPTZ", "rates_updated_at": "TIMESTAMPTZ", "finished_at": "TIMESTAMPTZ"}
# A record from before payers, which called the copy's date `anthem_updated_at`.
_LEGACY_RECORD = {("anthem_updated_at" if c == "updated_at" else c): t for c, t in _RECORD.items()}


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


class Payer:
    """One payer's raw rates, rate groups and rosters by file, each file's parse
    record, the index's files and plan links, Georgia's networks and plans, and
    the targets file."""

    def __init__(self, root, name):
        self.root = root
        self.name = name
        self.targets = f"{root}/targets.yaml"
        self.out = f"{root}/ingestion/payer_rates/{name}"
        self.rates = {}   # (file_id, label) -> rows
        self.groups = {}  # file_id -> rows
        self.rosters = {}  # file_id -> rows
        self.files = {}   # file_id -> name, the index's file list
        self.links = []
        self.plans = []
        if not os.path.exists(self.targets):
            with open(self.targets, "w") as f:
                f.write(TARGETS)
        _write(f"{root}/ingestion/reference/exchange_networks_ga.parquet",
               {"network_id": "VARCHAR", "issuer_id": "VARCHAR", "name": "VARCHAR"},
               [("45334-GAN001", "45334", "Blue Value"), ("49046-GAN008", "49046", "Pathway"),
                ("58081-GAN001", "58081", "Individual Georgia HMO"), ("70893-GAN001", "70893", "Premier"),
                ("70893-GAN003", "70893", "Select Plus")])

    def rate(self, file_id, label, group_set=1, code="70551", rate=250.0, billing_class="professional"):
        """Add a rate in file `file_id` under network label `label`."""
        rows = self.rates.setdefault((file_id, label), [])
        rows.append((file_id, group_set, label, "CPT", code, "ffs", "negotiated", rate, "9999-12-31", "11",
                     billing_class, "", "outpatient"))
        self._write_rates(file_id, label)

    def remove_rate(self, file_id, label):
        del self.rates[(file_id, label)]
        os.remove(self._rates_path(file_id, label))

    def remove_parquet(self, file_id):
        """Take every row parse wrote for file `file_id` away, as if no parse of it finished."""
        for file, label in list(self.rates):
            if file == file_id:
                self.remove_rate(file, label)
        for kind in ("group_sets", "providers"):
            os.remove(f"{self.root}/raw/{self.name}/{kind}/{file_id}.parquet")
        self.groups.pop(file_id)
        self.rosters.pop(file_id)

    def file(self, file_id, name):
        """List file `file_id` in this month's index as `name`."""
        self.files[file_id] = name
        _write(f"{self.root}/raw/{self.name}/index_files.parquet",
               {"file_id": "BIGINT", "name": "VARCHAR", "address": "VARCHAR", "location": "VARCHAR"},
               [(f, n, f"https://{self.name}/{n}", f"https://{self.name}/{n}?Expires=1")
                for f, n in sorted(self.files.items())])

    def record(self, file_id, status, reason="", read=OCT31, rates=OCT31, columns=_RECORD):
        """Save file `file_id`'s parse record: what its latest parse made of it,
        the copy it read and the copy whose rates are on disk."""
        name = self.files[file_id]
        row = {"file_id": file_id, "name": name, "address": f"https://{self.name}/{name}", "status": status,
               "reason": reason, "updated_at": read, "anthem_updated_at": read, "rates_updated_at": rates,
               "finished_at": "2026-11-07 18:14:12+00"}
        _write(f"{self.root}/raw/{self.name}/parses/{_stem(name)}.parquet", columns,
               [tuple(row[c] for c in columns)])

    def remove_record(self, file_id):
        os.remove(f"{self.root}/raw/{self.name}/parses/{_stem(self.files[file_id])}.parquet")

    def _rates_path(self, file_id, label):
        slug = "".join(ch if ch.isalnum() else "-" for ch in label.strip().lower())
        return f"{self.root}/raw/{self.name}/prices/net={slug}/{file_id}.parquet"

    def _write_rates(self, file_id, label):
        _write(self._rates_path(file_id, label), {
            "file_id": "BIGINT", "group_set_id": "BIGINT", "network_name": "VARCHAR",
            "billing_code_type": "VARCHAR", "billing_code": "VARCHAR", "negotiation_arrangement": "VARCHAR",
            "negotiated_type": "VARCHAR", "negotiated_rate": "DOUBLE", "expiration_date": "VARCHAR",
            "service_code": "VARCHAR", "billing_class": "VARCHAR", "modifier": "VARCHAR", "setting": "VARCHAR"},
            self.rates[(file_id, label)])

    def group(self, file_id, group_set, group, tin, *npis, label="any network", tin_type="npi"):
        """Put provider group `group`, billing as `tin`, with doctors `npis`, in
        `group_set` of file `file_id`. A group's label can list several networks."""
        self.groups.setdefault(file_id, []).append((file_id, group_set, group))
        self.rosters.setdefault(file_id, []).extend((file_id, group, label, npi, tin_type, tin) for npi in npis)
        _write(f"{self.root}/raw/{self.name}/group_sets/{file_id}.parquet",
               {"file_id": "BIGINT", "group_set_id": "BIGINT", "provider_group_id": "BIGINT"},
               self.groups[file_id])
        _write(f"{self.root}/raw/{self.name}/providers/{file_id}.parquet",
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
        _write(f"{self.root}/raw/{self.name}/index_file_plans.parquet", {"file_id": "BIGINT", "plan_id": "VARCHAR"},
               self.links)

    def plan(self, plan_id, network_id):
        """Add a plan on `network_id` to Georgia's plan file."""
        self.plans.append((plan_id, network_id))
        self._write_plans()

    def remove_plans(self, network_id):
        self.plans = [p for p in self.plans if p[1] != network_id]
        self._write_plans()

    def _write_plans(self):
        _write(f"{self.root}/ingestion/reference/exchange_plans_ga.parquet", {"plan_id": "VARCHAR", "network_id": "VARCHAR"},
               self.plans)

    def add_target(self, text):
        """Add a target to the targets file, as YAML list items."""
        with open(self.targets, "a") as f:
            f.write(text)

    def publish(self):
        from ingestion.payer_rates import publish
        return publish(self.root, self.out, self.name, self.targets)

    def rows(self, name):
        return duckdb.connect().execute(f"FROM '{self.out}/{name}.parquet' ORDER BY ALL").fetchall()

    def networks(self):
        """networks.parquet, with each file_updated_at as a UTC date."""
        con = duckdb.connect()
        con.execute("SET TimeZone = 'UTC'")
        return con.execute(f"SELECT network_id, status, file, strftime(file_updated_at, '%Y-%m-%d'), reason "
                           f"FROM '{self.out}/networks.parquet' ORDER BY network_id").fetchall()


def _stem(name):
    """A file's name without `.json.gz` or `.json`, which names its parse record."""
    return name.removesuffix(".gz").removesuffix(".json")


@pytest.fixture()
def anthem(tmp_path, monkeypatch):
    """Blue Value in file 21057 (GA_BV) and Pathway beside an untargeted network
    in file 701 (GA_PW), each the file its target names, linked to its
    network's plans and parsed from Anthem's 31 October copy, and one priced
    group in each."""
    monkeypatch.delenv("DUCKDB_TMP", raising=False)
    a = Payer(str(tmp_path), "anthem")
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
    for file_id, name in [(21057, "GA_BV.json.gz"), (701, "GA_PW.json.gz")]:
        a.file(file_id, name)
        a.record(file_id, "parsed")
    return a


@pytest.fixture()
def ambetter(tmp_path, monkeypatch):
    """Ambetter's one plain-JSON file (file 5001), which the index links to its
    plans by product ID and two targets name by a dated pattern: Premier's
    rates under "Exchange GA" and Select Plus's under "Exchange Select Plus",
    beside another issuer's label that no target names. Each has a group billing
    as an EIN."""
    monkeypatch.delenv("DUCKDB_TMP", raising=False)
    m = Payer(str(tmp_path), "ambetter")
    m.plan("70893GA0010001", "70893-GAN001")
    m.plan("70893GA0030002", "70893-GAN003")
    m.rate(5001, "Exchange GA", group_set=1)
    m.rate(5001, "Exchange Select Plus", group_set=2, rate=300.0)
    m.rate(5001, "Exchange Solutions", group_set=3, rate=999.0)
    m.group(5001, 1, 10, "86-2226051", 3000000001, label="Exchange GA|Exchange Select Plus", tin_type="ein")
    m.group(5001, 2, 20, "86-2226051", 3000000002, label="Exchange Select Plus", tin_type="ein")
    m.group(5001, 3, 30, "86-2226051", 3000000003, label="Exchange Solutions", tin_type="ein")
    m.link(5001, "70893GA001", "70893GA003", "45495GA004")
    m.file(5001, AMBETTER_FILE)
    m.record(5001, "parsed")
    return m
