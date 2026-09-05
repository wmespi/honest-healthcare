"""Raw + reference Parquet -> the serving tables (docs/architecture.md 2b).

One re-runnable pass. Every product decision that used to live in a serving-layer
SQL string is here: the outpatient-professional `scope`, the sentinel-price rule
(`serving/routers/rates.py:_sentinel_ceiling`), the Medicare benchmark
(`serving/benchmark.py`), the service-line allowlists (`serving/service_lines.py`),
the browse rollup (`scripts/build_rate_summary.py`), and AGENTS.md rule 5
conflict resolution.

Outputs, under SERVING_DIR (else <data-dir>/serving):

  rates/net=<slug>/part.parquet   one prices x group_sets row + the product
      columns; rule 5 collapses exact-duplicate lines across files
  group_members.parquet           (file_id, provider_group_id, npi, tin_value)
  provider_dim.parquet            NPPES GA + NUCC + DAC + geocode + service lines
  code_dim.parquet                RBCS label + category + MPFS + shoppable flag
  evidence.parquet                (npi, billing_code, tier)  billed | typical
  cross_network_rollup.parquet    (code, network) -> n_groups, p10, median, p90

Defaults to the network partitions that carry a target plan (etl/targets.yaml
`network_patterns`), so the build stays on the plan-scoped path — the full
`prices x group_sets` fan-out of the current store is ~33 B rows. `--networks`
overrides; `--all-networks` forces every partition. Each partition is processed
alone; a single-file one streams straight through, a multi-file one runs the
rule-5 aggregate. Reads the shared corpus read-only; writes only SERVING_DIR.
"""
import argparse
import fnmatch
import os
import re
import shutil
import time

import duckdb

from reference._common import serving_dir as _serving_dir, store_dir
# The curated service-line taxonomy lists. Still under serving/ this step —
# #100 moves them here when it repoints the API onto provider_dim.service_lines
# and serving no longer needs the constant. (The prod serving image ships only
# serving/, so serving can't import from build/ yet.)
from serving.service_lines import SERVICE_LINES

# serving/data_sources.outpatient_scope — the slice the consumer rate views
# compare. Kept in sync by hand (no shared module across the stack boundary).
OUTPATIENT_PROF = (
    "billing_class = 'professional' "
    "AND setting IN ('outpatient', 'both') "
    "AND negotiation_arrangement = 'ffs' "
    "AND negotiated_type IN ('fee schedule', 'negotiated')"
)


def _connect(spill):
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET memory_limit = '{os.getenv('DUCKDB_MEMORY_LIMIT', '4GB')}'")
    con.execute(f"SET threads = {os.getenv('DUCKDB_THREADS', '4')}")
    os.makedirs(spill, exist_ok=True)
    con.execute(f"SET temp_directory = '{spill}'")
    con.execute(f"SET max_temp_directory_size = '{os.getenv('DUCKDB_TMP_MAX', '120GiB')}'")
    return con


_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _target_network_globs(path=None):
    """The `network_patterns` globs from every target in etl/targets.yaml — the
    same signal the parse probe uses. `make build` defaults to the network
    partitions these match, so it stays on the plan-scoped path and never tries
    to fan out a 2 B-row off-exchange shard. [] means 'no filter' (build all)."""
    path = path or os.path.join(_REPO, "etl", "targets.yaml")
    if not os.path.exists(path):
        return []
    globs, in_block = [], False
    for line in open(path, encoding="utf-8"):
        s = line.rstrip()
        if re.match(r"\s*network_patterns:\s*$", s):
            in_block = True
            continue
        if in_block:
            m = re.match(r"\s*-\s*(.+?)\s*$", s)
            if m:
                globs.append(m.group(1).strip().strip('"').strip("'").lower())
            elif s.strip() and not s.startswith((" ", "\t")):
                in_block = False
            elif s.strip() and re.match(r"\s*\w+:", s):
                in_block = False
    return globs


def _service_line_expr(col):
    """concat_ws skips the NULL arms, so this is '' or 'pcp,...'; NULLIF -> NULL."""
    arms = []
    for name, codes in SERVICE_LINES.items():
        lst = ", ".join(f"'{c}'" for c in codes)
        arms.append(f"CASE WHEN {col} IN ({lst}) THEN '{name}' END")
    return f"NULLIF(concat_ws(',', {', '.join(arms)}), '')"


def _load_plan_counts(con, test, override=None):
    """Populate a temp table `file_plan_count(file_id, plan_count)` — the count
    of GA-individual plans the master index links each file to (Step 1's
    `index_file_plans`). Drives rule 5's plan_specific vs shared: absent until
    `make discover` has re-run post-#108, and then every row is `shared` and
    rule 5 is just exact-dup removal. Read over the DuckDB postgres extension so
    the build keeps no pg driver of its own; `override` (a {file_id: count}
    dict) skips the DB entirely, for tests. Returns True when counts loaded."""
    if override is not None:
        con.execute("CREATE TEMP TABLE file_plan_count (file_id BIGINT, plan_count BIGINT)")
        con.executemany("INSERT INTO file_plan_count VALUES (?, ?)", list(override.items()))
        return bool(override)

    url = os.getenv("TEST_DATABASE_URL" if test else "DATABASE_URL")
    if not url:
        print("  plan link: no DATABASE_URL — every rate tagged source_kind=shared")
        return False
    try:
        con.execute("INSTALL postgres; LOAD postgres")
        con.execute(f"ATTACH '{url}' AS pg (TYPE postgres, READ_ONLY)")
        con.execute("""
            CREATE TEMP TABLE file_plan_count AS
            SELECT file_id, COUNT(DISTINCT plan_id) AS plan_count
            FROM pg.index_file_plans GROUP BY file_id
        """)
        n = con.execute("SELECT COUNT(*) FROM file_plan_count").fetchone()[0]
        con.execute("DETACH pg")
    except duckdb.Error as e:
        print(f"  plan link: unreadable ({str(e).splitlines()[0]}) — source_kind=shared")
        return False
    if not n:
        print("  plan link: index_file_plans is empty — source_kind=shared")
        return False
    print(f"  plan link: {n:,} files carry a plan count")
    return True


def build(data_dir, serving_dir, networks=None, all_networks=False, test=False,
          plan_counts=None, targets_path=None):
    anthem = store_dir(data_dir, test, "anthem", "ANTHEM_DIR")
    nppes = store_dir(data_dir, test, "nppes", "NPPES_DIR")
    ref = store_dir(data_dir, test, "reference", "REFERENCE_DIR")
    cms = store_dir(data_dir, test, "cms", "CMS_DIR")
    # Spill under the (always-writable) output dir — never the shared corpus,
    # which a worktree mounts read-only. Cleaned on the way out.
    spill = os.getenv("DUCKDB_TMP") or f"{serving_dir}/.spill"

    prices_dir = f"{anthem}/prices"
    ref_p = {k: f"{ref}/{v}" for k, v in {
        "labels": "code_labels.parquet", "nucc": "nucc_taxonomy.parquet",
        "mpfs": "mpfs_ga.parquet", "dac": "dac_ga.parquet",
        "geocode": "pcp_geocode.parquet", "profiles": "specialty_procedure_profiles.parquet",
    }.items()}
    cms_util = f"{cms}/ga_provider_service.parquet"
    ga_nppes = f"{nppes}/ga_providers.parquet"

    all_parts = sorted(d for d in os.listdir(prices_dir) if d.startswith("net="))
    if networks:
        want = {n.replace("net=", "") for n in networks}
        parts = [d for d in all_parts if d[4:] in want]
        if not parts:
            raise SystemExit(f"no price partitions match {sorted(want)}")
    elif all_networks:
        parts = all_parts
        print(f"  --all-networks: {len(parts)} partitions, no plan scope")
    else:
        globs = _target_network_globs(targets_path)
        if not globs:
            parts = all_parts
            print(f"  no targets.yaml network_patterns — building all {len(parts)} partitions")
        else:
            _c = duckdb.connect()
            parts = []
            for d in all_parts:
                nm = _c.execute(
                    f"SELECT network_name FROM read_parquet('{prices_dir}/{d}/*.parquet') LIMIT 1"
                ).fetchone()
                if nm and any(fnmatch.fnmatchcase(nm[0].lower(), g) for g in globs):
                    parts.append(d)
            _c.close()
            skipped = len(all_parts) - len(parts)
            print(f"  targets.yaml network_patterns {globs} → {len(parts)} of "
                  f"{len(all_parts)} partitions ({skipped} off-target skipped; "
                  f"--all-networks to force)")
            if not parts:
                raise SystemExit("no partition matches a targets.yaml network_pattern")

    part_scan = ("read_parquet([" + ", ".join(
        f"'{prices_dir}/{d}/*.parquet'" for d in parts) + "], "
        "hive_partitioning=1, union_by_name=true)")

    rates_out = f"{serving_dir}/rates"
    shutil.rmtree(rates_out, ignore_errors=True)
    shutil.rmtree(spill, ignore_errors=True)  # a crashed prior run can leak it
    os.makedirs(serving_dir, exist_ok=True)

    con = _connect(spill)
    have_plan_link = _load_plan_counts(con, test, plan_counts)
    src_kind = ("CASE WHEN fpc.plan_count = 1 THEN 'plan_specific' ELSE 'shared' END"
                if have_plan_link else "'shared'")
    plan_join = ("LEFT JOIN file_plan_count fpc ON fpc.file_id = pr.file_id"
                 if have_plan_link else "")

    t0 = time.time()

    # ── code sentinel ceiling: max($1, 5% of the code's scoped median rate),
    #    off price rows (pre-fan-out) to match _sentinel_ceiling. Scoped to the
    #    selected partitions — for a target-only run that is the target network.
    con.execute(f"""
        CREATE TEMP TABLE code_ceiling AS
        SELECT billing_code_type, billing_code,
               greatest(1.0, round(0.05 * median(negotiated_rate), 2)) AS ceiling
        FROM {part_scan}
        WHERE {OUTPATIENT_PROF} AND negotiated_rate > 0
        GROUP BY 1, 2
    """)

    # ── MPFS nonfacility allowed $ per (code, modifier), GA median across localities
    if os.path.exists(ref_p["mpfs"]):
        con.execute(f"""
            CREATE TEMP TABLE mpfs AS
            SELECT billing_code, billing_code_type, COALESCE(modifier, '') AS modifier,
                   round(median(medicare_allowed), 2) AS medicare_allowed
            FROM read_parquet('{ref_p["mpfs"]}')
            WHERE pos = 'nonfacility' AND medicare_allowed IS NOT NULL
            GROUP BY 1, 2, 3
        """)
    else:
        con.execute("CREATE TEMP TABLE mpfs (billing_code VARCHAR, "
                    "billing_code_type VARCHAR, modifier VARCHAR, medicare_allowed DOUBLE)")

    # ── rates, one network partition at a time.
    #
    # A single-file partition (the target, and most others) has no cross-file
    # conflict and no exact-duplicate expansion rows — rule 5 is a no-op, so the
    # join streams straight to Parquet in constant memory. A multi-file
    # partition runs the rule-5 hash aggregate: collapse only the exact-duplicate
    # rate lines CMS's "publish every file" mandate creates (`_pick` keeps the
    # plan-specific file's row, else the lowest file_id). Genuinely different
    # rates for one line (different rosters, a plan discount) are all kept;
    # `source_kind` lets the read layer prefer plan-specific / lower-shared
    # without flattening the spread the quote/compare views aggregate over.
    # build.md documents this. The aggregate holds a multi-file partition's whole
    # key space in memory+spill, so a very large off-target multi-file partition
    # (only reachable via --all-networks) can still exhaust it — #94 removes
    # those from the store.
    grp = ("provider_group_id, billing_code_type, billing_code, modifier, setting, "
           "service_code, negotiated_rate, negotiated_type, negotiation_arrangement, "
           "expiration_date, scope, is_sentinel, medicare_allowed, vs_medicare, net")
    total_rows = total_dropped = 0
    for d in parts:
        slug = d[4:]
        pt = time.time()
        pq = f"read_parquet('{prices_dir}/{d}/*.parquet', hive_partitioning=1)"
        fids = [r[0] for r in con.execute(f"SELECT DISTINCT file_id FROM {pq}").fetchall()]
        gs_files = [f"{anthem}/group_sets/{f}.parquet" for f in fids
                    if os.path.exists(f"{anthem}/group_sets/{f}.parquet")]
        if not gs_files:
            print(f"  rates net={slug}: no group_sets — skipped")
            continue
        gs_list = "[" + ", ".join(f"'{f}'" for f in gs_files) + "]"
        os.makedirs(f"{rates_out}/net={slug}", exist_ok=True)

        line_cte = f"""
            WITH line AS (
                SELECT pr.network_name, pr.net, pr.file_id, gs.provider_group_id,
                       pr.billing_code_type, pr.billing_code,
                       COALESCE(pr.modifier, '') AS modifier, pr.setting,
                       pr.service_code, pr.negotiation_arrangement, pr.negotiated_type,
                       pr.expiration_date, pr.negotiated_rate,
                       CASE WHEN {OUTPATIENT_PROF} THEN 'outpatient_prof' ELSE 'other' END AS scope,
                       pr.negotiated_rate <= COALESCE(cc.ceiling, 1.0) AS is_sentinel,
                       {src_kind} AS source_kind,
                       CAST(CASE WHEN {src_kind} = 'plan_specific' THEN 0 ELSE 1 END AS BIGINT)
                         * 100000000000 + pr.file_id AS _pick,
                       mp.medicare_allowed,
                       CASE WHEN mp.medicare_allowed > 0
                            THEN round(pr.negotiated_rate / mp.medicare_allowed, 2) END AS vs_medicare
                FROM {pq} pr
                JOIN read_parquet({gs_list}, union_by_name=true) gs
                  ON gs.file_id = pr.file_id AND gs.group_set_id = pr.group_set_id
                LEFT JOIN code_ceiling cc
                  ON cc.billing_code_type = pr.billing_code_type AND cc.billing_code = pr.billing_code
                {plan_join}
                LEFT JOIN mpfs mp
                  ON mp.billing_code = pr.billing_code
                 AND mp.billing_code_type = pr.billing_code_type
                 AND mp.modifier = COALESCE(pr.modifier, '')
            )"""
        cols = ("network_name, file_id, provider_group_id, billing_code_type, billing_code, "
                "modifier, setting, service_code, negotiated_rate, negotiated_type, "
                "negotiation_arrangement, expiration_date, scope, is_sentinel, source_kind, "
                "medicare_allowed, vs_medicare, net")
        if len(fids) > 1:
            body = f"""
                SELECT any_value(network_name) AS network_name, arg_min(file_id, _pick) AS file_id,
                       provider_group_id, billing_code_type, billing_code, modifier, setting,
                       service_code, negotiated_rate, negotiated_type, negotiation_arrangement,
                       expiration_date, scope, is_sentinel, arg_min(source_kind, _pick) AS source_kind,
                       medicare_allowed, vs_medicare, net
                FROM line GROUP BY {grp}"""
        else:
            body = f"SELECT {cols} FROM line"
        con.execute(f"COPY ({line_cte} {body}) "
                    f"TO '{rates_out}/net={slug}/part.parquet' (FORMAT parquet, COMPRESSION zstd)")

        kept = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{rates_out}/net={slug}/part.parquet')").fetchone()[0]
        if len(fids) > 1:
            raw = con.execute(f"""
                WITH r AS (SELECT file_id, group_set_id, COUNT(*) AS n
                           FROM read_parquet({gs_list}, union_by_name=true) GROUP BY 1, 2)
                SELECT COALESCE(SUM(r.n), 0) FROM {pq} p JOIN r USING (file_id, group_set_id)
            """).fetchone()[0]
        else:
            raw = kept
        total_rows += kept
        total_dropped += raw - kept
        print(f"  rates net={slug}: {kept:,} rows  (-{raw - kept:,} rule 5, {len(fids)} file"
              f"{'s' if len(fids) != 1 else ''})  {time.time() - pt:.1f}s")

    rates_glob = f"read_parquet('{rates_out}/**/*.parquet', hive_partitioning=1, union_by_name=true)"
    sel_fids = con.execute(f"SELECT DISTINCT file_id FROM {rates_glob}").fetchall()
    fid_list = ", ".join(str(r[0]) for r in sel_fids) or "NULL"

    # ── group_members ────────────────────────────────────────────────────────
    con.execute(f"""
        COPY (
            SELECT DISTINCT file_id, provider_group_id, npi, tin_value
            FROM read_parquet('{anthem}/providers/*.parquet', union_by_name=true)
            WHERE file_id IN ({fid_list})
        ) TO '{serving_dir}/group_members.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    # ── provider_dim ─────────────────────────────────────────────────────────
    dac = f"LEFT JOIN read_parquet('{ref_p['dac']}') d ON d.npi = g.npi" if os.path.exists(ref_p["dac"]) else ""
    geo = f"LEFT JOIN read_parquet('{ref_p['geocode']}') gc ON gc.npi = g.npi" if os.path.exists(ref_p["geocode"]) else ""
    nucc = f"LEFT JOIN read_parquet('{ref_p['nucc']}') nx ON nx.taxonomy_code = g.taxonomy_code" if os.path.exists(ref_p["nucc"]) else ""
    con.execute(f"""
        COPY (
            SELECT g.npi,
                   COALESCE(NULLIF(g.org_name, ''),
                            NULLIF(trim(BOTH ', ' FROM g.last_name || ', ' || g.first_name), '')) AS name,
                   {"COALESCE(nx.specialty, NULLIF(g.taxonomy_group, 'Other'))" if nucc else "NULLIF(g.taxonomy_group, 'Other')"} AS specialty,
                   {"nx.classification" if nucc else "NULL"} AS nucc_classification,
                   {"d.org_name" if dac else "NULL"} AS org_name,
                   {"d.org_pac_id" if dac else "NULL"} AS org_pac_id,
                   {"gc.latitude" if geo else "NULL"} AS lat,
                   {"gc.longitude" if geo else "NULL"} AS lon,
                   {_service_line_expr("g.taxonomy_code")} AS service_lines,
                   g.is_hospital, g.is_clinic, g.entity_type, g.city, g.postal_code
            FROM read_parquet('{ga_nppes}') g
            {nucc} {dac} {geo}
        ) TO '{serving_dir}/provider_dim.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    # ── code_dim ─────────────────────────────────────────────────────────────
    labels = f"LEFT JOIN read_parquet('{ref_p['labels']}') cl USING (billing_code_type, billing_code)" if os.path.exists(ref_p["labels"]) else ""
    con.execute(f"""
        COPY (
            WITH used AS (SELECT DISTINCT billing_code_type, billing_code FROM {rates_glob}),
                 nm AS (SELECT billing_code_type, billing_code, any_value(name) AS name
                        FROM read_parquet('{anthem}/codes/*.parquet') GROUP BY 1, 2),
                 mg AS (SELECT billing_code, billing_code_type, medicare_allowed
                        FROM mpfs WHERE modifier = '')
            SELECT u.billing_code, u.billing_code_type,
                   COALESCE({"cl.label, " if labels else ""}nm.name) AS label,
                   {"cl.rbcs_category" if labels else "NULL"} AS category,
                   (u.billing_code_type = 'CPT' AND regexp_full_match(u.billing_code, '[0-9]{{5}}')
                    AND u.billing_code NOT LIKE 'J%' AND u.billing_code NOT LIKE 'Q%') AS shoppable,
                   mg.medicare_allowed
            FROM used u
            {labels}
            LEFT JOIN nm USING (billing_code_type, billing_code)
            LEFT JOIN mg USING (billing_code, billing_code_type)
        ) TO '{serving_dir}/code_dim.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    # ── evidence: (npi, billing_code, tier) for NPIs reachable through a rate.
    #    billed (this NPI billed it to Medicare) wins over typical (>=3% of the
    #    NPI's NUCC classification bill it — serving/evidence.py's tiers).
    ev_parts = []
    if os.path.exists(cms_util):
        ev_parts.append(f"""
            SELECT DISTINCT npi, hcpcs_cd AS billing_code, 'billed' AS tier
            FROM read_parquet('{cms_util}')
            WHERE npi IN (SELECT npi FROM read_parquet('{serving_dir}/group_members.parquet'))
        """)
    if os.path.exists(cms_util) and os.path.exists(ref_p["profiles"]) and nucc:
        ev_parts.append(f"""
            SELECT DISTINCT s.npi, sp.hcpcs_cd AS billing_code, 'typical' AS tier
            FROM (
                SELECT DISTINCT g.npi, nx.classification
                FROM read_parquet('{ga_nppes}') g
                JOIN read_parquet('{ref_p["nucc"]}') nx ON nx.taxonomy_code = g.taxonomy_code
                WHERE g.npi IN (SELECT npi FROM read_parquet('{serving_dir}/group_members.parquet'))
            ) s
            JOIN read_parquet('{ref_p["profiles"]}') sp ON sp.specialty = s.classification
            WHERE sp.prevalence >= 0.03
              AND NOT EXISTS (
                SELECT 1 FROM read_parquet('{cms_util}') b
                WHERE b.npi = s.npi AND b.hcpcs_cd = sp.hcpcs_cd)
        """)
    if ev_parts:
        con.execute(f"COPY ({' UNION ALL '.join(ev_parts)}) "
                    f"TO '{serving_dir}/evidence.parquet' (FORMAT parquet, COMPRESSION zstd)")
    else:
        con.execute("CREATE TEMP TABLE _ev (npi BIGINT, billing_code VARCHAR, tier VARCHAR)")
        con.execute(f"COPY _ev TO '{serving_dir}/evidence.parquet' (FORMAT parquet, COMPRESSION zstd)")

    # ── cross_network_rollup — replaces summary/ and /rates/by_network's scan.
    con.execute(f"""
        COPY (
            SELECT billing_code, billing_code_type, net, any_value(network_name) AS network_name,
                   count(DISTINCT provider_group_id) AS n_groups,
                   round(quantile_cont(negotiated_rate, 0.1), 2) AS p10,
                   round(median(negotiated_rate), 2) AS median,
                   round(quantile_cont(negotiated_rate, 0.9), 2) AS p90
            FROM {rates_glob}
            WHERE scope = 'outpatient_prof' AND NOT is_sentinel AND modifier = ''
            GROUP BY 1, 2, 3
        ) TO '{serving_dir}/cross_network_rollup.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    def n(name):
        return con.execute(f"SELECT COUNT(*) FROM read_parquet('{serving_dir}/{name}')").fetchone()[0]

    print(f"\n  rates                  {total_rows:>12,}  (-{total_dropped:,} rule 5)")
    for f in ("group_members.parquet", "provider_dim.parquet", "code_dim.parquet",
              "evidence.parquet", "cross_network_rollup.parquet"):
        print(f"  {f:<22} {n(f):>12,}")
    print(f"\n  total {time.time() - t0:.1f}s -> {serving_dir}/")
    con.close()
    shutil.rmtree(spill, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR", "/app/data"))
    ap.add_argument("--serving-dir", default=None,
                    help="output dir (default: SERVING_DIR env, else <data-dir>/serving)")
    ap.add_argument("--networks", default=None,
                    help="comma-separated net slugs to build "
                         "(default: partitions matching a targets.yaml network_pattern)")
    ap.add_argument("--all-networks", action="store_true",
                    help="build every partition, ignoring the targets.yaml scope "
                         "(the off-target store fans out to billions of rows)")
    ap.add_argument("--targets", default=None, help="a targets.yaml other than etl/targets.yaml")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    data_dir = "data-test" if args.test else args.data_dir
    serving_dir = args.serving_dir or _serving_dir(data_dir, args.test)
    networks = [s.strip() for s in args.networks.split(",")] if args.networks else None

    if not os.path.isdir(f"{store_dir(data_dir, args.test, 'anthem', 'ANTHEM_DIR')}/prices"):
        raise SystemExit("no rate store — run `make parse` first")
    build(data_dir, serving_dir, networks, args.all_networks, args.test,
          targets_path=args.targets)


if __name__ == "__main__":
    main()
