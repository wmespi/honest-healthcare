"""The find-care serving tables (#135), written beside the old ones until those go.

`build` is the table of contents: it lists every table in the order they're
built, each with its key and the function that selects its rows. The file then
reads top-down: the tables, the build-only steps they read, where the data comes
from, and how a table is checked and written.

Every table's key is checked before it is written, and a failed check stops the
build. Rows are written in key order, so two builds from the same inputs write
the same bytes. Nothing here imports from serving/.
"""
import fnmatch
import os
import shutil

import duckdb
import yaml

from reference._common import store_dir, write_parquet_atomic

TARGETS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "etl", "targets.yaml")


def build(data_dir, serving_dir, test=False, targets_path=TARGETS_PATH):
    """Write every find-care table under `serving_dir`; returns {table: rows}."""
    sources = _Sources(data_dir, test)
    spill = os.getenv("DUCKDB_TMP") or f"{serving_dir}/.spill"
    con = connect(spill)

    # Build-only steps, which the tables below read but which aren't written.
    _network_files(con, sources, read_targets(targets_path))

    # The tables, in build order: (table, key, the function that selects its rows).
    # A table may read the ones above it.
    tables = [
        ("networks", ["network_id"], _networks),
    ]
    rows = {}
    for table, key, select in tables:
        con.execute(f"CREATE TEMP TABLE {table} AS {select(sources)}")
        rows[table] = _write(con, table, key, serving_dir)
        print(f"  {table + '.parquet':<22} {rows[table]:>13,}")

    con.close()
    if os.getenv("DUCKDB_TMP") is None:  # only a spill dir we own
        shutil.rmtree(spill, ignore_errors=True)
    return rows


# ── The tables ────────────────────────────────────────────────────────────────

def _networks(sources):
    """networks.parquet (network_id, name): one row per network a target prices,
    with Georgia's exchange name for it."""
    return f"""
        SELECT network_id, name
        FROM {sources.exchange_networks()}
        WHERE network_id IN (SELECT network_id FROM network_files)
    """


# ── Build-only steps ──────────────────────────────────────────────────────────

def _network_files(con, sources, targets):
    """`network_files` (network_id, file_id, network_name): for each network a
    target names, the parsed insurer file whose rates price it and the labels
    those rates carry. A target's `network_patterns` match labels the way the
    parse probe matches them: case-insensitive, `*` wildcard. Stops unless
    Georgia lists the network and exactly one file carries its labels
    (etl/mrf-model.md, "One plan, one contract")."""
    georgia = {r[0] for r in con.execute(
        f"SELECT network_id FROM {sources.exchange_networks()}").fetchall()}
    labels = con.execute(f"SELECT DISTINCT file_id, network_name FROM {sources.insurer_rates()}").fetchall()
    rows = []
    for name, nets, patterns in targets:
        hits = sorted({(fid, label) for fid, label in labels
                       if any(fnmatch.fnmatchcase((label or "").lower(), p.lower()) for p in patterns)})
        files = sorted({fid for fid, _ in hits})
        for net in nets:
            if net not in georgia:
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
    _check_key(con, "network_files", ["network_id", "network_name"])


# ── Where the data comes from ─────────────────────────────────────────────────

def read_targets(path=TARGETS_PATH):
    """[(name, exchange_networks, network_patterns)] for each target in etl/targets.yaml."""
    with open(path) as f:
        targets = (yaml.safe_load(f) or {}).get("targets") or []
    return [(t.get("name"), t.get("exchange_networks") or [], t.get("network_patterns") or [])
            for t in targets]


class _Sources:
    """The datasets the find-care tables are built from, one method each, read
    from `<data_dir>/...` or `data-test/...` under --test. A method returns a
    DuckDB table expression, or stops the build with the command that makes its
    dataset. A table that needs another dataset adds a method here."""

    def __init__(self, data_dir, test):
        self._data_dir = data_dir
        self._test = test

    def insurer_rates(self):
        """Every rate `make parse` has written from the insurers' files, across
        all network partitions (data/anthem/prices)."""
        prices = f"{store_dir(self._data_dir, self._test, 'anthem')}/prices"
        if not os.path.isdir(prices):
            raise SystemExit(f"no {prices}: run `make parse`")
        return f"read_parquet('{prices}/*/*.parquet', hive_partitioning=1, union_by_name=true)"

    def exchange_networks(self):
        """Georgia's exchange networks (network_id, issuer_id, name), from
        reference/exchange-plans.md."""
        return self._reference("exchange_networks_ga.parquet", "exchange-plans")

    def _reference(self, file, step):
        path = f"{store_dir(self._data_dir, self._test, 'reference')}/{file}"
        if not os.path.exists(path):
            raise SystemExit(f"no {path}: run `make reference STEP={step}`")
        return f"read_parquet('{path}')"


# ── Checking and writing ──────────────────────────────────────────────────────

def _check_key(con, table, key):
    """Stop the build if any column of `key` is empty or a key repeats in `table`."""
    cols = ", ".join(key)
    empty = con.execute(f"SELECT count(*) FROM {table} WHERE "
                        + " OR ".join(f"{c} IS NULL" for c in key)).fetchone()[0]
    if empty:
        raise SystemExit(f"{table}: {empty} rows with an empty key ({cols})")
    dup = con.execute(f"SELECT {cols} FROM {table} GROUP BY {cols} HAVING count(*) > 1 LIMIT 3").fetchall()
    if dup:
        raise SystemExit(f"{table}: the key ({cols}) repeats, for example {dup}")


def _write(con, table, key, out_dir):
    """Check `table`'s key, write it to `<out_dir>/<table>.parquet` in key order
    (so the same rows always make the same bytes), and return its row count."""
    _check_key(con, table, key)
    write_parquet_atomic(con, f"SELECT * FROM {table} ORDER BY {', '.join(key)}",
                         f"{out_dir}/{table}.parquet")
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


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
