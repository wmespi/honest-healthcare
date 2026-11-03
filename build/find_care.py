"""The find-care serving tables (#135), written beside the old ones until those go.

Each table is one function that reads raw and reference Parquet, checks its key
and what it points at, and writes one file under the serving directory. A table
that fails a check stops the build before it is written, and two builds from the
same inputs write the same bytes. Nothing here imports from serving/.

  networks.parquet   (network_id, name)  each network a target in
      etl/targets.yaml prices, with Georgia's exchange name for it

Which insurer file prices a network is build config: the target's
`network_patterns` name the labels its rates carry, and exactly one parsed file
may carry them (etl/mrf-model.md, "One plan, one contract").
"""
import fnmatch
import os
import shutil

import duckdb
import yaml

from reference._common import store_dir, write_parquet_atomic

TARGETS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "etl", "targets.yaml")


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


def build(data_dir, serving_dir, test=False, targets_path=TARGETS_PATH):
    """Write every find-care table under `serving_dir`; returns {table: rows}."""
    inputs = Inputs(data_dir, test)
    spill = os.getenv("DUCKDB_TMP") or f"{serving_dir}/.spill"
    con = connect(spill)
    network_files(con, inputs, read_targets(targets_path))
    rows = {"networks": networks(con, inputs, serving_dir)}
    con.close()
    if os.getenv("DUCKDB_TMP") is None:  # only a spill dir we own
        shutil.rmtree(spill, ignore_errors=True)
    for table, n in rows.items():
        print(f"  {table + '.parquet':<22} {n:>13,}")
    return rows


class Inputs:
    """Where the build reads from: `<data_dir>/...`, or `data-test/...` under --test.
    Each method returns a DuckDB table expression, or stops the build with the
    command that makes the missing input."""

    def __init__(self, data_dir, test):
        self.anthem = store_dir(data_dir, test, "anthem")
        self.ref = store_dir(data_dir, test, "reference")

    def reference(self, name, step):
        """`data/reference/<name>.parquet`, which `make reference STEP=<step>` writes."""
        path = f"{self.ref}/{name}.parquet"
        if not os.path.exists(path):
            raise SystemExit(f"no {path}: run `make reference STEP={step}`")
        return f"read_parquet('{path}')"

    def insurer_rates(self):
        """Every parsed insurer rate, across all network partitions."""
        if not os.path.isdir(f"{self.anthem}/prices"):
            raise SystemExit(f"no {self.anthem}/prices: run `make parse`")
        return (f"read_parquet('{self.anthem}/prices/*/*.parquet', "
                f"hive_partitioning=1, union_by_name=true)")


def read_targets(path=TARGETS_PATH):
    """[(name, exchange_networks, network_patterns)] for each target in etl/targets.yaml."""
    with open(path) as f:
        targets = (yaml.safe_load(f) or {}).get("targets") or []
    return [(t.get("name"), t.get("exchange_networks") or [], t.get("network_patterns") or [])
            for t in targets]


def network_files(con, inputs, targets):
    """Build-only `network_files` (network_id, file_id, network_name): for each
    network a target names, the parsed insurer file whose rates price it and the
    labels those rates carry. Labels match a target's `network_patterns` the way
    the parse probe matches them: case-insensitive, `*` wildcard."""
    known = {r[0] for r in con.execute(
        f"SELECT network_id FROM {inputs.reference('exchange_networks_ga', 'exchange-plans')}").fetchall()}
    labels = con.execute(f"SELECT DISTINCT file_id, network_name FROM {inputs.insurer_rates()}").fetchall()
    rows = []
    for name, nets, patterns in targets:
        hits = sorted({(fid, label) for fid, label in labels
                       if any(fnmatch.fnmatchcase((label or "").lower(), p.lower()) for p in patterns)})
        files = sorted({fid for fid, _ in hits})
        for net in nets:
            if net not in known:
                raise SystemExit(f"{name}: {net} isn't a network in Georgia's exchange files")
            if not files:
                raise SystemExit(f"{name}: no parsed insurer file carries a label matching "
                                 f"{patterns}, so nothing prices {net}; run `make parse`")
            if len(files) > 1:
                raise SystemExit(f"{name}: files {files} all carry a label matching {patterns}, "
                                 f"but one file prices {net} (etl/mrf-model.md#one-plan-one-contract)")
            rows += [(net, fid, label) for fid, label in hits]
    con.execute("CREATE TEMP TABLE network_files (network_id VARCHAR, file_id BIGINT, network_name VARCHAR)")
    if rows:
        con.executemany("INSERT INTO network_files VALUES (?, ?, ?)", rows)
    check_key(con, "network_files", ["network_id", "network_name"])


def networks(con, inputs, out_dir):
    """networks.parquet: one row per network in `network_files`, named by Georgia's exchange."""
    con.execute(f"""
        CREATE TEMP TABLE networks AS
        SELECT network_id, name
        FROM {inputs.reference('exchange_networks_ga', 'exchange-plans')}
        WHERE network_id IN (SELECT network_id FROM network_files)
    """)
    return write(con, "networks", ["network_id"], out_dir)


def check_key(con, table, key):
    """Stop the build if any column of `key` is empty or a key repeats in `table`."""
    cols = ", ".join(key)
    empty = con.execute(f"SELECT count(*) FROM {table} WHERE "
                        + " OR ".join(f"{c} IS NULL" for c in key)).fetchone()[0]
    if empty:
        raise SystemExit(f"{table}: {empty} rows with an empty key ({cols})")
    dup = con.execute(f"SELECT {cols} FROM {table} GROUP BY {cols} HAVING count(*) > 1 LIMIT 3").fetchall()
    if dup:
        raise SystemExit(f"{table}: the key ({cols}) repeats, for example {dup}")


def write(con, table, key, out_dir):
    """Check `table`'s key, write it to `<out_dir>/<table>.parquet` in key order
    (so the same rows always make the same bytes), and return its row count."""
    check_key(con, table, key)
    write_parquet_atomic(con, f"SELECT * FROM {table} ORDER BY {', '.join(key)}",
                         f"{out_dir}/{table}.parquet")
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
