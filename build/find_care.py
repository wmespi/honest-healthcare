"""The find-care serving tables (#135), written beside the old ones until those go.

Each table is a class that says what the table is: its name, key and columns,
and the SELECT that makes its rows. `_Table.make` runs that SELECT and checks
the columns, the key and the table's own rules, so no table can skip a check.
`build` lists the tables in the order they're made, and a table may read the
ones above it. Every table is made and checked before any file is written, so a
failed check leaves the last build's files as they were.

The file reads top-down: `build`, what a table is, the tables, the inputs they
read, then the shared pieces. Rows are written in key order, so two builds from
the same inputs write the same bytes. Nothing here imports from serving/.
"""
import glob
import os
import shutil

import duckdb
import yaml

from reference._common import write_parquet_atomic

TARGETS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "etl", "targets.yaml")


def build(data_dir, serving_dir, targets_path=TARGETS_PATH):
    """Write every find-care table under `serving_dir` from the inputs under
    `data_dir` (`data`, or `data-test` for a test build); returns {table: rows}."""
    spill = os.getenv("DUCKDB_TMP") or f"{serving_dir}/.spill"
    con = connect(spill)
    tables = [
        _Targets(targets_path),
        _NetworkFiles(),
        _Networks(),
    ]
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


# ── What a table is ───────────────────────────────────────────────────────────

class _Table:
    """A table the build makes. A subclass sets `name`, `key` and `columns`
    (name -> DuckDB type, in order) and writes `select`, plus `check` for any
    rule beyond its key. A build-only table sets `written = False`: the tables
    after it read it, but it isn't saved."""
    name: str
    key: list
    columns: dict
    written = True

    def select(self, data_dir):
        """The SELECT that makes this table's rows."""
        raise NotImplementedError

    def fill(self, con, data_dir):
        """Make the table in `con` from its SELECT."""
        con.execute(f"CREATE TEMP TABLE {self.name} AS {self.select(data_dir)}")

    def check(self, con, data_dir):
        """Stop the build if the rows break a rule of this table's."""

    def make(self, con, data_dir):
        """Make the table in `con`, then stop the build unless its columns match
        `columns`, its key is never empty or repeated, and `check` passes."""
        self.fill(con, data_dir)
        got = {c: t for c, t, *_ in con.execute(f"DESCRIBE {self.name}").fetchall()}
        if list(got.items()) != list(self.columns.items()):
            raise SystemExit(f"{self.name}: columns {got}, but the table says {self.columns}")
        cols = ", ".join(self.key)
        empty = con.execute(f"SELECT count(*) FROM {self.name} WHERE "
                            + " OR ".join(f"{c} IS NULL" for c in self.key)).fetchone()[0]
        if empty:
            raise SystemExit(f"{self.name}: {empty} rows with an empty key ({cols})")
        dup = con.execute(f"SELECT {cols} FROM {self.name} GROUP BY {cols} "
                          f"HAVING count(*) > 1 LIMIT 3").fetchall()
        if dup:
            raise SystemExit(f"{self.name}: the key ({cols}) repeats, for example {dup}")
        self.check(con, data_dir)

    def write(self, con, out_dir):
        """Write the table to `<out_dir>/<name>.parquet` in key order, so the
        same rows always make the same bytes. Returns its row count."""
        write_parquet_atomic(con, f"SELECT * FROM {self.name} ORDER BY {', '.join(self.key)}",
                             f"{out_dir}/{self.name}.parquet")
        return con.execute(f"SELECT count(*) FROM {self.name}").fetchone()[0]


# ── The tables, in the order they're made ─────────────────────────────────────

class _Targets(_Table):
    """Build-only: one row per network a target in etl/targets.yaml names, with
    the insurer network labels its rates carry. `patterns` are as written, and
    `likes` are the same patterns as SQL LIKE patterns. Every network must be
    one that Georgia's exchange lists."""
    name = "targets"
    key = ["network_id"]
    columns = {"target": "VARCHAR", "network_id": "VARCHAR",
               "patterns": "VARCHAR[]", "likes": "VARCHAR[]"}
    written = False

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
            WHERE NOT EXISTS (SELECT 1 FROM {_EXCHANGE_NETWORKS.read(data_dir)} g
                              WHERE g.network_id = t.network_id)
            ORDER BY t.network_id
        """).fetchone()
        if unknown:
            raise SystemExit(f"{unknown[0]}: {unknown[1]} isn't a network in Georgia's exchange files")


class _NetworkFiles(_Table):
    """Build-only: for each target network, the parsed insurer file whose rates
    price it and the labels those rates carry. Labels match a target's patterns
    the way the parse probe matches them (etl/extraction/targets.go): trimmed,
    case-insensitive, and `*` is the only wildcard. Exactly one file must carry
    them (etl/mrf-model.md, "One plan, one contract")."""
    name = "network_files"
    key = ["network_id", "file_id", "network_name"]
    columns = {"network_id": "VARCHAR", "file_id": "BIGINT", "network_name": "VARCHAR"}
    written = False

    def select(self, data_dir):
        return f"""
            WITH labels AS (SELECT DISTINCT file_id, network_name FROM {_INSURER_RATES.read(data_dir)}),
                 likes AS (SELECT network_id, unnest(likes) AS label_like FROM targets)
            SELECT DISTINCT k.network_id, l.file_id, l.network_name
            FROM likes k
            JOIN labels l ON lower(trim(l.network_name)) LIKE k.label_like ESCAPE '!'
        """

    def check(self, con, data_dir):
        bad = con.execute("""
            SELECT t.target, t.network_id, t.patterns,
                   list(DISTINCT f.file_id ORDER BY f.file_id) FILTER (WHERE f.file_id IS NOT NULL)
            FROM targets t LEFT JOIN network_files f USING (network_id)
            GROUP BY t.target, t.network_id, t.patterns
            HAVING count(DISTINCT f.file_id) != 1
            ORDER BY t.network_id
        """).fetchone()
        if bad:
            target, net, patterns, files = bad
            if not files:
                raise SystemExit(f"{target}: no parsed insurer file carries a label matching "
                                 f"{patterns}, so nothing prices {net}; run `make parse`")
            raise SystemExit(f"{target}: files {files} all carry a label matching {patterns}, "
                             f"but one file prices {net} (etl/mrf-model.md#one-plan-one-contract). "
                             f"Delete the Parquet under data/anthem/ of any that don't, "
                             f"whether an older copy or another contract")


class _Networks(_Table):
    """networks.parquet: one row per network a target prices, with Georgia's
    exchange name for it."""
    name = "networks"
    key = ["network_id"]
    columns = {"network_id": "VARCHAR", "name": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            SELECT network_id, name
            FROM {_EXCHANGE_NETWORKS.read(data_dir)}
            WHERE network_id IN (SELECT network_id FROM network_files)
        """


# ── The inputs the tables read ────────────────────────────────────────────────

class _Input:
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
_INSURER_RATES = _Input("anthem/prices/*/*.parquet", "make parse",
                        ", hive_partitioning=1, union_by_name=true")
# Georgia's exchange networks: network_id, issuer_id, name (reference/exchange-plans.md).
_EXCHANGE_NETWORKS = _Input("reference/exchange_networks_ga.parquet",
                            "make reference STEP=exchange-plans")


def read_targets(path=TARGETS_PATH):
    """[(name, exchange_networks, network_patterns)] for each target in etl/targets.yaml."""
    with open(path) as f:
        targets = (yaml.safe_load(f) or {}).get("targets") or []
    return [(t.get("name"), t.get("exchange_networks") or [], t.get("network_patterns") or [])
            for t in targets]


# ── Shared pieces ─────────────────────────────────────────────────────────────

def _like(pattern):
    """A target label pattern as a lower-case SQL LIKE pattern with `!` as its
    escape: `*` becomes `%`, and LIKE's own `%` and `_` match only themselves."""
    if pattern is None:
        return None
    escaped = pattern.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return escaped.replace("*", "%")


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
