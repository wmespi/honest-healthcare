"""Anthem's rates by Georgia network: the last step of `make parse`, which turns
the files parse streamed (data/raw/anthem/) into the payer rates integration reads
(data/ingestion/payer_rates/anthem/, docs/schema.md).

A target in ingestion/etl/targets.yaml names Georgia exchange networks, the files that
price them and the labels those files give them. A network's rates are the
ones under one of its labels in the one parsed file its target names that the
index links to the network's plans, and every plan on the network has to be
linked to that file (ingestion/etl/mrf-model.md, "One plan, one contract").
A target's shared labels, other labels in that file that price the plan's
members without its name, add all their rates with the groups they cover;
`own_label` says which rates are under the network's own labels.
A network no parsed file prices is left out, and networks.parquet says why, so
one file's failure stales only its own networks. A mistake in the targets file
or the index stops the step, and so does a run that prices no network at all,
so integration never sees Anthem's labels, files or index.
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
_PARSES = ("raw/anthem/parses/*.parquet", "make parse", ", union_by_name=true")
_INDEX_FILES = ("raw/anthem/index_files.parquet", "make discover", "")
_INDEX_LINKS = ("raw/anthem/index_file_plans.parquet", "make discover", "")
_EXCHANGE_NETWORKS = ("ingestion/reference/exchange_networks_ga.parquet", "make reference STEP=exchange-plans", "")
_EXCHANGE_PLANS = ("ingestion/reference/exchange_plans_ga.parquet", "make reference STEP=exchange-plans", "")

# The payer rates, as integration reads them (integration/inputs/datasets.py). Each
# SELECT reads the `network_files` table publish() makes: every
# rate under a network's own and shared labels is published, and `own_label` says
# which. Rates and rate groups
# are written in full row order, so the same files make the same bytes; a
# network's rosters can run to hundreds of millions of rows (every doctor in
# every provider group its rates use), so they're streamed as they come.
_RATES = f"""
    SELECT f.network_id, r.group_set_id, r.billing_code_type, r.billing_code,
           r.negotiation_arrangement, r.negotiated_type, r.negotiated_rate, r.expiration_date,
           r.service_code, r.billing_class, r.modifier, r.setting, f.own_label
    FROM {{rates}} r JOIN network_files f USING (file_id, network_name)
    ORDER BY ALL
"""
_RATE_GROUPS = f"""
    SELECT DISTINCT n.network_id, g.group_set_id, g.provider_group_id
    FROM {{groups}} g
    JOIN (SELECT DISTINCT f.network_id, r.file_id, r.group_set_id
          FROM {{rates}} r JOIN network_files f USING (file_id, network_name)) n
      USING (file_id, group_set_id)
    ORDER BY ALL
"""
_ROSTERS = f"""
    SELECT n.network_id, p.provider_group_id, p.npi, p.tin_type, p.tin_value
    FROM {{rosters}} p
    JOIN (SELECT DISTINCT f.network_id, r.file_id, g.provider_group_id
          FROM {{rates}} r JOIN network_files f USING (file_id, network_name)
          JOIN {{groups}} g USING (file_id, group_set_id)) n
      USING (file_id, provider_group_id)
"""
# One row per target network, saying whether its rates are from the latest copy
# of its file. `networks` is the table _networks() makes.
_NETWORKS = "SELECT * FROM networks ORDER BY network_id"

_BAR = "═" * 70


def publish(data_dir, out_dir, targets_path=TARGETS_PATH):
    """Write rates.parquet, rate_groups.parquet, rosters.parquet and
    networks.parquet under `out_dir` from the files under `data_dir`; returns
    {name: rows}. They're written beside `out_dir` and moved into place
    together, and nothing is written if a check fails."""
    spill = os.getenv("DUCKDB_TMP") or f"{out_dir}.spill"
    os.makedirs(spill, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET memory_limit = '{os.getenv('DUCKDB_MEMORY_LIMIT', '4GB')}'")
    con.execute(f"SET temp_directory = '{spill}'")
    con.execute("SET TimeZone = 'UTC'")
    try:
        _targets(con, data_dir, targets_path)
        _named_files(con, data_dir, targets_path)
        _network_files(con, data_dir, targets_path)
        _check_plans(con, data_dir)
        _records(con, data_dir)
        _networks(con)
        paths = {"rates": _read(data_dir, _RAW_RATES), "groups": _read(data_dir, _RAW_GROUPS),
                 "rosters": _read(data_dir, _RAW_ROSTERS)}
        tables = {"rates": _RATES, "rate_groups": _RATE_GROUPS, "rosters": _ROSTERS, "networks": _NETWORKS}
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
    """`targets`: one row per network a target names, with the files it names
    and its labels as SQL LIKE patterns. Every network has to be one of
    Georgia's, with a plan on it, and named by one target that names files."""
    rows = [(name, (net or "").strip(), [f.strip() for f in files], patterns, [_like(p) for p in patterns],
             [_like(p) for p in shared])
            for name, nets, files, patterns, shared in read_targets(targets_path) for net in nets]
    if not rows:
        raise SystemExit(f"{targets_path} names no exchange networks")
    blank = [name for name, net, *_ in rows if not net]
    if blank:
        raise SystemExit(f"{targets_path}: target {blank[0]!r} names a blank exchange network")
    fileless = [name for name, _, files, *_ in rows if not files]
    if fileless:
        raise SystemExit(f"{targets_path}: target {fileless[0]!r} names no files, so nothing can price its networks")
    con.execute("CREATE TEMP TABLE targets (target VARCHAR, network_id VARCHAR, files VARCHAR[], "
                "patterns VARCHAR[], likes VARCHAR[], shared_likes VARCHAR[])")
    con.executemany("INSERT INTO targets VALUES (?, ?, ?, ?, ?, ?)", rows)
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


def _named_files(con, data_dir, targets_path):
    """`named_files`: (network_id, file_id, name) for each file a target names
    that this month's index links to a plan on the network: the files that can
    price it. A network with none stops the step, since nothing it names prices it."""
    con.execute(f"""
        CREATE TEMP TABLE named_files AS
        SELECT DISTINCT t.network_id, i.file_id, i.name
        FROM (SELECT network_id, unnest(files) AS name FROM targets) t
        JOIN {_read(data_dir, _INDEX_FILES)} i USING (name)
        JOIN {_read(data_dir, _INDEX_LINKS)} l USING (file_id)
        JOIN {_read(data_dir, _EXCHANGE_PLANS)} p ON p.plan_id = l.plan_id AND p.network_id = t.network_id
    """)
    unnamed = con.execute("""
        SELECT target, network_id, files FROM targets
        WHERE network_id NOT IN (SELECT network_id FROM named_files) ORDER BY network_id
    """).fetchone()
    if unnamed:
        target, net, files = unnamed
        raise SystemExit(f"{target}: this month's index doesn't link {', '.join(files)} to a plan on {net}, so "
                         f"nothing {targets_path} names prices it. Name the file that does, and run `make parse`")


def _network_files(con, data_dir, targets_path):
    """`network_files`: (network_id, file_id, network_name, own_label) for
    each label a target network's rates carry in a parsed file that can price
    it, and `labels`: every label in each parsed file. Labels match the way the
    parse probe matches them (ingestion/etl/extraction/targets.go): trimmed,
    case-insensitive, and `*` is the only wildcard. A shared label counts only
    in a file that carries one of the network's own labels, and `own_label`
    says whether a label is one of the network's own. Two parsed files
    carrying one network's labels stop the step: one file prices a network."""
    if glob.glob(f"{data_dir}/{_RAW_RATES[0]}"):
        con.execute(f"CREATE TEMP TABLE labels AS "
                    f"SELECT DISTINCT file_id, network_name FROM {_read(data_dir, _RAW_RATES)}")
    else:
        con.execute("CREATE TEMP TABLE labels (file_id BIGINT, network_name VARCHAR)")
    con.execute("""
        CREATE TEMP TABLE network_files AS
        WITH own AS (
            SELECT DISTINCT k.network_id, l.file_id, l.network_name
            FROM (SELECT network_id, unnest(likes) AS label_like FROM targets) k
            JOIN named_files n USING (network_id)
            JOIN labels l ON l.file_id = n.file_id AND lower(trim(l.network_name)) LIKE k.label_like ESCAPE '!'
        )
        SELECT network_id, file_id, network_name, true AS own_label FROM own
        UNION ALL
        SELECT DISTINCT k.network_id, l.file_id, l.network_name, false
        FROM (SELECT network_id, unnest(shared_likes) AS label_like FROM targets) k
        JOIN (SELECT DISTINCT network_id, file_id FROM own) o USING (network_id)
        JOIN labels l ON l.file_id = o.file_id AND lower(trim(l.network_name)) LIKE k.label_like ESCAPE '!'
        WHERE NOT EXISTS (SELECT 1 FROM own w WHERE w.network_id = k.network_id AND w.file_id = l.file_id
                                                AND w.network_name = l.network_name)
    """)
    two = con.execute("""
        SELECT t.target, t.network_id, t.patterns, list(DISTINCT n.name ORDER BY n.name)
        FROM targets t JOIN network_files f USING (network_id) JOIN named_files n USING (network_id, file_id)
        GROUP BY t.target, t.network_id, t.patterns
        HAVING count(DISTINCT f.file_id) > 1
        ORDER BY t.network_id
    """).fetchone()
    if two:
        target, net, patterns, names = two
        raise SystemExit(f"{target}: {targets_path} names {', '.join(names)}, which the index links to plans on "
                         f"{net} and which each carry a label matching {patterns}, but one file prices {net} "
                         f"(ingestion/etl/mrf-model.md#one-plan-one-contract). Name only the one that does")


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


def _records(con, data_dir):
    """`records`: what the latest parse of each file made of it
    (ingestion/etl/parse.md#when-parse-downloads-a-file), with `rates_from`, when
    Anthem updated the copy whose rates are on disk. A parsed record's rates
    are the copy it read."""
    if not glob.glob(f"{data_dir}/{_PARSES[0]}"):
        con.execute("CREATE TEMP TABLE records (file_id BIGINT, status VARCHAR, reason VARCHAR, "
                    "finished_at TIMESTAMPTZ, rates_from TIMESTAMPTZ)")
        return
    source = _read(data_dir, _PARSES)
    columns = {c for c, *_ in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
    kept = "rates_updated_at" if "rates_updated_at" in columns else "NULL::TIMESTAMPTZ"
    con.execute(f"""
        CREATE TEMP TABLE records AS
        SELECT file_id, status, reason, finished_at,
               coalesce({kept}, CASE WHEN status = 'parsed' THEN anthem_updated_at END) AS rates_from
        FROM {source}
    """)


def _networks(con):
    """`networks`: one row per target network. Its rates are `current`, from the
    latest copy of its file; `stale`, from an earlier copy because the latest
    parse failed or skipped it; or `missing`, because no parsed file prices it,
    and then it has none. `file` is the file they're from, `file_updated_at`
    when Anthem updated that copy, and `reason` says why they aren't current.
    A run with no network priced stops the step: everything after it needs one."""
    files = con.execute("""
        SELECT n.network_id, t.patterns, n.file_id, n.name, n.file_id IN (SELECT file_id FROM network_files
                                                                         WHERE network_id = n.network_id),
               r.status, r.reason, strftime(r.finished_at, '%Y-%m-%d %H:%M:%S'),
               (SELECT list(DISTINCT network_name ORDER BY network_name) FROM labels l WHERE l.file_id = n.file_id)
        FROM named_files n JOIN targets t USING (network_id) LEFT JOIN records r USING (file_id)
        ORDER BY n.network_id, n.name
    """).fetchall()
    by_network = {}
    for net, patterns, *f in files:
        by_network.setdefault(net, (patterns, []))[1].append(f)
    rows = []
    for net, (patterns, candidates) in by_network.items():
        priced = [c for c in candidates if c[2]]
        if not priced:
            rows.append((net, "missing", None, None, "; ".join(_why(c, patterns) for c in candidates)))
            continue
        file_id, name, _, status, *_ = priced[0]
        if status == "parsed":
            rows.append((net, "current", file_id, name, None))
        elif status is None:
            rows.append((net, "stale", file_id, name, f"{name} has no parse record, so nothing says which copy "
                                                      f"its rates are from"))
        else:
            rows.append((net, "stale", file_id, name, _why(priced[0], patterns)))
    con.execute("CREATE TEMP TABLE statuses (network_id VARCHAR, status VARCHAR, file_id BIGINT, "
                "file VARCHAR, reason VARCHAR)")
    con.executemany("INSERT INTO statuses VALUES (?, ?, ?, ?, ?)", rows)
    con.execute("""
        CREATE TEMP TABLE networks AS
        SELECT s.network_id, s.status, s.file, r.rates_from AS file_updated_at, s.reason
        FROM statuses s LEFT JOIN records r USING (file_id)
    """)
    if all(status == "missing" for _, status, *_ in rows):
        raise SystemExit("no target network has rates, so there's nothing to publish:\n" + "\n".join(
            f"  {net}: {reason}" for net, _, _, _, reason in sorted(rows)))


def _why(candidate, patterns):
    """Why a file that can price a network has no current rates for it."""
    _, name, _, status, reason, finished, labels = candidate
    if status is None:
        return f"{name} hasn't been parsed"
    if status == "failed":
        return f"the latest parse of {name}, on {finished}, failed: {reason}"
    if status == "skipped":
        return f"the latest parse of {name}, on {finished}, skipped it: {reason}"
    if not labels:
        return f"{name} has no parsed rates on disk"
    shown = ", ".join(labels[:5]) + (f" and {len(labels) - 5} more" if len(labels) > 5 else "")
    return f"{name} carries no label matching {patterns}, only {shown}"


def warning(out_dir):
    """What the published networks.parquet under `out_dir` says about every
    network whose rates aren't from Anthem's latest copy, as a banner, or ""."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    rows = con.execute(f"""
        SELECT network_id, status, file, strftime(file_updated_at, '%Y-%m-%d'), reason
        FROM '{out_dir}/networks.parquet' WHERE status != 'current' ORDER BY network_id
    """).fetchall()
    con.close()
    if not rows:
        return ""
    lines = ["⚠️  NETWORKS WITHOUT RATES FROM ANTHEM'S LATEST COPY"]
    for net, status, file, as_of, reason in rows:
        if status == "missing":
            lines.append(f"{net} has no rates, so nothing is published for it: {reason}")
        else:
            lines.append(f"{net}'s rates are from the copy of {file} Anthem updated on "
                         f"{as_of or 'a date it did not give'}: {reason}")
    lines.append("Every other network is published as usual.")
    return "\n".join([f"╔{_BAR}", *(f"║ {line}" for line in lines), f"╚{_BAR}"])


def _swap(staged, out_dir):
    """Move the staged folder into place, so a reader sees the old files or the
    new ones, never a mix."""
    old = f"{out_dir}.old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(out_dir):
        os.rename(out_dir, old)
    os.rename(staged, out_dir)
    shutil.rmtree(old, ignore_errors=True)


def read_targets(path=TARGETS_PATH):
    """[(name, exchange_networks, files, network_patterns, shared_patterns)]
    for each target in ingestion/etl/targets.yaml."""
    with open(path) as f:
        targets = (yaml.safe_load(f) or {}).get("targets") or []
    return [(t.get("name"), t.get("exchange_networks") or [], t.get("files") or [],
             t.get("network_patterns") or [], t.get("shared_patterns") or []) for t in targets]


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
    out_dir = store_dir(data_dir, args.test, "payer_rates/anthem")
    rows = publish(data_dir, out_dir, args.targets)
    for name, n in rows.items():
        print(f"  payer_rates/anthem/{name + '.parquet':<20} {n:>13,}")
    banner = warning(out_dir)
    if banner:
        print(banner)


if __name__ == "__main__":
    main()
