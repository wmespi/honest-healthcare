"""Anthem's rates by Georgia network: the last step of `make parse`, which turns
the files parse streamed (data/raw/anthem/) into the payer rates integration reads
(data/ingestion/payer_rates/anthem/, docs/schema.md).

A target in ingestion/etl/targets.yaml names Georgia exchange networks, the files that
price them and the labels those files give them. A network's rates are the
ones under one of its labels in the one parsed file the index links to the
network's plans, and every plan on the network has to be linked to that file
(ingestion/etl/mrf-model.md, "One plan, one contract"). Anything else stops the step, so
integration never sees Anthem's labels, files or index.
"""
import argparse
import glob
import os
import shutil

import duckdb
import yaml

from ingestion.reference._common import store_dir

TARGETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "etl", "targets.yaml")

# What parse and discover wrote, and Georgia's plan file: (path under the data
# dir, the command that makes it, read_parquet options).
_RAW_RATES = ("raw/anthem/prices/*/*.parquet", "make parse", ", hive_partitioning=1, union_by_name=true")
_RAW_GROUPS = ("raw/anthem/group_sets/*.parquet", "make parse", "")
_RAW_ROSTERS = ("raw/anthem/providers/*.parquet", "make parse", "")
_INDEX_LINKS = ("raw/anthem/index_file_plans.parquet", "make discover", "")
_EXCHANGE_NETWORKS = ("ingestion/reference/exchange_networks_ga.parquet", "make reference STEP=exchange-plans", "")
_EXCHANGE_PLANS = ("ingestion/reference/exchange_plans_ga.parquet", "make reference STEP=exchange-plans", "")

# The payer rates, as integration reads them (integration/inputs/datasets.py). Each
# SELECT reads the `network_files` table publish() makes. Rates and rate groups
# are written in full row order, so the same files make the same bytes; a
# network's rosters can run to hundreds of millions of rows (every doctor in
# every provider group its rates use), so they're streamed as they come.
_RATES = """
    SELECT f.network_id, r.group_set_id, r.billing_code_type, r.billing_code,
           r.negotiation_arrangement, r.negotiated_type, r.negotiated_rate, r.expiration_date,
           r.service_code, r.billing_class, r.modifier, r.setting
    FROM {rates} r JOIN network_files f USING (file_id, network_name)
    ORDER BY ALL
"""
_RATE_GROUPS = """
    SELECT DISTINCT n.network_id, g.group_set_id, g.provider_group_id
    FROM {groups} g
    JOIN (SELECT DISTINCT f.network_id, r.file_id, r.group_set_id
          FROM {rates} r JOIN network_files f USING (file_id, network_name)) n
      USING (file_id, group_set_id)
    ORDER BY ALL
"""
_ROSTERS = """
    SELECT n.network_id, p.provider_group_id, p.npi, p.tin_type, p.tin_value
    FROM {rosters} p
    JOIN (SELECT DISTINCT f.network_id, r.file_id, g.provider_group_id
          FROM {rates} r JOIN network_files f USING (file_id, network_name)
          JOIN {groups} g USING (file_id, group_set_id)) n
      USING (file_id, provider_group_id)
"""


def publish(data_dir, out_dir, targets_path=TARGETS_PATH):
    """Write rates.parquet, rate_groups.parquet and rosters.parquet under
    `out_dir` from the files under `data_dir`; returns {name: rows}. They're
    written beside `out_dir` and moved into place together, and nothing is
    written if a check fails."""
    spill = os.getenv("DUCKDB_TMP") or f"{out_dir}.spill"
    os.makedirs(spill, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET memory_limit = '{os.getenv('DUCKDB_MEMORY_LIMIT', '4GB')}'")
    con.execute(f"SET temp_directory = '{spill}'")
    try:
        _targets(con, data_dir, targets_path)
        _network_files(con, data_dir)
        _check_plans(con, data_dir)
        tables = {"rates": _RATES, "rate_groups": _RATE_GROUPS, "rosters": _ROSTERS}
        paths = {"rates": _read(data_dir, _RAW_RATES), "groups": _read(data_dir, _RAW_GROUPS),
                 "rosters": _read(data_dir, _RAW_ROSTERS)}
        staged = f"{out_dir}.tmp"
        shutil.rmtree(staged, ignore_errors=True)
        os.makedirs(staged)
        rows = {}
        for name, select in tables.items():
            con.execute(f"COPY ({select.format(**paths)}) "
                        f"TO '{staged}/{name}.parquet' (FORMAT parquet, COMPRESSION zstd)")
            rows[name] = con.execute(f"SELECT count(*) FROM '{staged}/{name}.parquet'").fetchone()[0]
        _swap(staged, out_dir)
        return rows
    finally:
        con.close()
        if not os.getenv("DUCKDB_TMP"):
            shutil.rmtree(spill, ignore_errors=True)


def _read(data_dir, dataset):
    """A DuckDB table expression for one of the inputs above, or stop with the
    command that makes it."""
    path, made_by, options = dataset
    full = f"{data_dir}/{path}"
    if not glob.glob(full):
        raise SystemExit(f"no {full}: run `{made_by}`")
    return f"read_parquet('{full}'{options})"


def _targets(con, data_dir, targets_path):
    """`targets`: one row per network a target names, with its labels as SQL
    LIKE patterns. Every network has to be one of Georgia's, with a plan on it,
    and named by one target."""
    rows = [(name, (net or "").strip(), patterns, [_like(p) for p in patterns])
            for name, nets, patterns in read_targets(targets_path) for net in nets]
    if not rows:
        raise SystemExit(f"{targets_path} names no exchange networks")
    blank = [name for name, net, *_ in rows if not net]
    if blank:
        raise SystemExit(f"{targets_path}: target {blank[0]!r} names a blank exchange network")
    con.execute("CREATE TEMP TABLE targets (target VARCHAR, network_id VARCHAR, "
                "patterns VARCHAR[], likes VARCHAR[])")
    con.executemany("INSERT INTO targets VALUES (?, ?, ?, ?)", rows)
    twice = con.execute("SELECT network_id, list(target ORDER BY target) FROM targets "
                        "GROUP BY network_id HAVING count(*) > 1 ORDER BY network_id").fetchone()
    if twice:
        raise SystemExit(f"{targets_path}: targets {twice[1]} each name {twice[0]}, which one target has to")
    unknown = con.execute(f"""
        SELECT t.target, t.network_id FROM targets t
        WHERE NOT EXISTS (SELECT 1 FROM {_read(data_dir, _EXCHANGE_NETWORKS)} g
                          WHERE g.network_id = t.network_id)
        ORDER BY t.network_id
    """).fetchone()
    if unknown:
        raise SystemExit(f"{unknown[0]}: {unknown[1]} isn't a network in Georgia's exchange files")
    planless = con.execute(f"""
        SELECT t.target, t.network_id FROM targets t
        WHERE NOT EXISTS (SELECT 1 FROM {_read(data_dir, _EXCHANGE_PLANS)} p
                          WHERE p.network_id = t.network_id)
        ORDER BY t.network_id
    """).fetchone()
    if planless:
        raise SystemExit(f"{planless[0]}: no plan in Georgia's exchange plan file is on {planless[1]}")


def _network_files(con, data_dir):
    """`network_files`: (network_id, file_id, network_name) for the parsed file
    that prices each target network and the labels its rates carry there.
    Labels match the way the parse probe matches them (ingestion/etl/extraction/targets.go):
    trimmed, case-insensitive, and `*` is the only wildcard."""
    labelled = f"""
        SELECT DISTINCT k.network_id, l.file_id, l.network_name
        FROM (SELECT network_id, unnest(likes) AS label_like FROM targets) k
        JOIN (SELECT DISTINCT file_id, network_name FROM {_read(data_dir, _RAW_RATES)}) l
          ON lower(trim(l.network_name)) LIKE k.label_like ESCAPE '!'
    """
    con.execute(f"""
        CREATE TEMP TABLE network_files AS
        WITH labelled AS ({labelled}),
             linked AS (SELECT DISTINCT p.network_id, l.file_id
                        FROM {_read(data_dir, _EXCHANGE_PLANS)} p
                        JOIN {_read(data_dir, _INDEX_LINKS)} l USING (plan_id))
        SELECT network_id, file_id, network_name FROM labelled JOIN linked USING (network_id, file_id)
    """)
    bad = con.execute("""
        SELECT t.target, t.network_id, t.patterns,
               list(DISTINCT f.file_id ORDER BY f.file_id) FILTER (WHERE f.file_id IS NOT NULL)
        FROM targets t LEFT JOIN network_files f USING (network_id)
        GROUP BY t.target, t.network_id, t.patterns
        HAVING count(DISTINCT f.file_id) != 1
        ORDER BY t.network_id
    """).fetchone()
    if not bad:
        return
    target, net, patterns, files = bad
    if files:
        raise SystemExit(f"{target}: the index links files {files} to plans on {net}, and each "
                         f"carries a label matching {patterns}, but one file prices {net} "
                         f"(ingestion/etl/mrf-model.md#one-plan-one-contract). Delete the Parquet under "
                         f"data/raw/anthem/ of any that don't, whether an older copy or another contract")
    unlinked = con.execute(f"SELECT list(DISTINCT l.file_id ORDER BY l.file_id) FROM ({labelled}) l "
                           f"WHERE l.network_id = ?", [net]).fetchone()[0]
    if unlinked:
        raise SystemExit(f"{target}: files {unlinked} carry a label matching {patterns}, but the "
                         f"index doesn't link them to a plan on {net}, so nothing prices it. Name "
                         f"the file it links in ingestion/etl/targets.yaml and run `make parse`")
    raise SystemExit(f"{target}: no parsed insurer file carries a label matching "
                     f"{patterns}, so nothing prices {net}; run `make parse`")


def _check_plans(con, data_dir):
    """Every plan on a target network is priced by the network's file, so the
    index has to link each one to that file."""
    unlinked = con.execute(f"""
        SELECT DISTINCT p.plan_id, p.network_id, f.file_id
        FROM {_read(data_dir, _EXCHANGE_PLANS)} p JOIN network_files f USING (network_id)
        WHERE NOT EXISTS (SELECT 1 FROM {_read(data_dir, _INDEX_LINKS)} l
                          WHERE l.file_id = f.file_id AND l.plan_id = p.plan_id)
        ORDER BY p.plan_id
    """).fetchone()
    if unlinked:
        plan, net, file_id = unlinked
        raise SystemExit(f"the index doesn't link {plan} to file {file_id}, which prices its "
                         f"network {net}, so that file's rates aren't its contract "
                         f"(ingestion/etl/mrf-model.md#one-plan-one-contract)")


def _swap(staged, out_dir):
    """Move the staged folder into place, so a reader sees the old three files
    or the new three, never a mix."""
    old = f"{out_dir}.old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(out_dir):
        os.rename(out_dir, old)
    os.rename(staged, out_dir)
    shutil.rmtree(old, ignore_errors=True)


def read_targets(path=TARGETS_PATH):
    """[(name, exchange_networks, network_patterns)] for each target in ingestion/etl/targets.yaml."""
    with open(path) as f:
        targets = (yaml.safe_load(f) or {}).get("targets") or []
    return [(t.get("name"), t.get("exchange_networks") or [], t.get("network_patterns") or [])
            for t in targets]


def _like(pattern):
    """A target label pattern as a lower-case SQL LIKE pattern with `!` as its
    escape: `*` becomes `%`, and LIKE's own `%` and `_` match only themselves."""
    escaped = pattern.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return escaped.replace("*", "%")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR", "/app/data"))
    ap.add_argument("--test", action="store_true", help="read and write under data-test/")
    ap.add_argument("--targets", default=TARGETS_PATH, help="the target-plan list (default ingestion/etl/targets.yaml)")
    args = ap.parse_args()
    data_dir = "data-test" if args.test else args.data_dir
    rows = publish(data_dir, store_dir(data_dir, args.test, "payer_rates/anthem"), args.targets)
    for name, n in rows.items():
        print(f"  payer_rates/anthem/{name + '.parquet':<20} {n:>13,}")


if __name__ == "__main__":
    main()
