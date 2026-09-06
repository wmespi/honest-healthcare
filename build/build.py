"""Raw + reference Parquet -> the serving tables (docs/architecture.md 2b).

One re-runnable pass. Every product decision that used to live in a serving-layer
SQL string is here: the outpatient-professional `scope`, the sentinel-price rule
(was `serving/routers/rates.py:_sentinel_ceiling`), the Medicare benchmark
(`serving/benchmark.py`), the service-line allowlists (`serving/service_lines.py`),
and the browse rollup (was `scripts/build_rate_summary.py`).

Outputs, under SERVING_DIR (else <data-dir>/serving) — the ONLY thing the API
reads (#100):

  rates/net=<slug>/part.parquet   one row per price (parser grain) + the
      enrichment columns; `group_set_id` links to group_sets
  group_sets.parquet              (file_id, group_set_id, provider_group_id)
  group_members.parquet           (file_id, provider_group_id, npi, tin_value)
  group_networks.parquet          (file_id, provider_group_id, net, network_name)
      — which networks a file-local provider group is attributed to
  provider_dim.parquet            NPPES GA + NUCC + DAC + geocode + service lines
  provider_affiliations.parquet   (npi, ccn, facility_name)  the DAC CCN<->NPI bridge
  code_dim.parquet                RBCS label + category + MPFS + shoppable flag
  evidence.parquet                (npi, billing_code, tier, ...)  billed rows carry
      the Medicare utilization detail, typical rows their prevalence
  rate_hist.parquet               roster-weighted $25 histogram per
      (net, code, setting, scope, modifier, is_sentinel) — the browse primitive
  cross_network_rollup.parquet    (code, network) -> n_groups, min/p10/median/p90/max
      off the rate_hist CDF — read straight by /rates/by_network
  manifest.json                   built_at, which inputs were present, row counts

`rates` is the parser's price grain — no group fan-out — so `make build` is a
routine full-store pass (expansion happens at query time after pruning on `net`
+ `billing_code`). Rule 5 keeps every row and tags `source_kind`; the read layer
resolves MRF redundancy per practice (#100). Reads the shared corpus read-only;
writes only SERVING_DIR.
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import time

import duckdb

from reference._common import serving_dir as _serving_dir, store_dir
# The curated service-line taxonomy lists. Still under serving/ this step —
# the prod serving image ships only serving/, so serving can't import from
# build/; a later tidy consolidates them.
from serving.service_lines import SERVICE_LINES
from serving.data_sources import network_slug

HIST_WIDTH = 25      # $ bucket width — matches the retired build_rate_summary
HIST_CAP = 5000      # rates >= this land in one overflow bucket
# A code billed by >= this share of a NUCC classification's Medicare providers
# is "typical" for that classification (evidence tier 2). The build floor —
# `evidence.prevalence` lets the read layer apply a stricter threshold, never a
# looser one.
TYPICAL_THRESHOLD = 0.03


# serving/data_sources.outpatient_scope — the slice the consumer rate views
# compare. Kept in sync by hand; serving/tests/test_build.py pins the two equal.
def _scope(a=""):
    a = f"{a}." if a else ""
    return (f"{a}billing_class = 'professional' "
            f"AND {a}setting IN ('outpatient', 'both') "
            f"AND {a}negotiation_arrangement = 'ffs' "
            f"AND {a}negotiated_type IN ('fee schedule', 'negotiated')")


OUTPATIENT_PROF = _scope()


def _connect(spill):
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET memory_limit = '{os.getenv('DUCKDB_MEMORY_LIMIT', '4GB')}'")
    con.execute(f"SET threads = {os.getenv('DUCKDB_THREADS', '4')}")
    os.makedirs(spill, exist_ok=True)
    con.execute(f"SET temp_directory = '{spill}'")
    con.execute(f"SET max_temp_directory_size = '{os.getenv('DUCKDB_TMP_MAX', '120GiB')}'")
    return con


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
    `index_file_plans`). It tags each `rates` row `source_kind` (plan_specific
    when plan_count = 1, else shared); absent until `make discover` has re-run
    post-#108, and then every row is `shared`. Read over the DuckDB postgres
    extension so the build keeps no pg driver of its own; `override` (a
    {file_id: count} dict) skips the DB entirely, for tests. Returns True when
    counts loaded."""
    if override is not None:
        con.execute("CREATE TEMP TABLE file_plan_count (file_id BIGINT, plan_count BIGINT)")
        if override:
            con.executemany("INSERT INTO file_plan_count VALUES (?, ?)", list(override.items()))
        return bool(override)

    url = os.getenv("TEST_DATABASE_URL" if test else "DATABASE_URL")
    if not url:
        print("  plan link: no DATABASE_URL — every rate tagged source_kind=shared")
        return False
    tbl = "pg.test.index_file_plans" if test else "pg.index_file_plans"
    try:
        con.execute("INSTALL postgres; LOAD postgres")
        con.execute(f"ATTACH '{url}' AS pg (TYPE postgres, READ_ONLY)")
        con.execute(f"""
            CREATE TEMP TABLE file_plan_count AS
            SELECT file_id, COUNT(DISTINCT plan_id) AS plan_count
            FROM {tbl} GROUP BY file_id
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


def build(data_dir, serving_dir, networks=None, test=False, plan_counts=None):
    anthem = store_dir(data_dir, test, "anthem", "ANTHEM_DIR")
    nppes = store_dir(data_dir, test, "nppes", "NPPES_DIR")
    ref = store_dir(data_dir, test, "reference", "REFERENCE_DIR")
    cms = store_dir(data_dir, test, "cms", "CMS_DIR")
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
    all_scan = ("read_parquet([" + ", ".join(
        f"'{prices_dir}/{d}/*.parquet'" for d in all_parts) + "], "
        "hive_partitioning=1, union_by_name=true)")
    if networks:
        want = {n[4:] if n.startswith("net=") else n for n in networks}
        parts = [d for d in all_parts if d[4:] in want]
        missing = want - {d[4:] for d in parts}
        if missing:
            raise SystemExit(f"no price partition for {sorted(missing)}")
    else:
        parts = all_parts

    rates_out = f"{serving_dir}/rates"
    for d in parts:
        shutil.rmtree(f"{rates_out}/net={d[4:]}", ignore_errors=True)
    if os.getenv("DUCKDB_TMP") is None:  # only a spill dir we own
        shutil.rmtree(spill, ignore_errors=True)
    os.makedirs(serving_dir, exist_ok=True)

    con = _connect(spill)
    have_plan_link = _load_plan_counts(con, test, plan_counts)
    src_kind = ("CASE WHEN fpc.plan_count = 1 THEN 'plan_specific' ELSE 'shared' END"
                if have_plan_link else "'shared'")
    plan_join = ("LEFT JOIN file_plan_count fpc ON fpc.file_id = pr.file_id"
                 if have_plan_link else "")

    t0 = time.time()

    # ── code sentinel ceiling: max($1, 5% of the code's scoped median rate).
    #    Store-wide (all networks), matching the retired _sentinel_ceiling; one
    #    scalar pass, approx_quantile keeps per-code state O(1).
    con.execute(f"""
        CREATE TEMP TABLE code_ceiling AS
        SELECT billing_code_type, billing_code,
               greatest(1.0, round(0.05 * approx_quantile(negotiated_rate, 0.5), 2)) AS ceiling
        FROM {all_scan}
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

    # ── rates — parser price grain + enrichment, one partition at a time. No
    #    group_sets join: expansion to provider groups happens at query time
    #    after pruning on `net` + `billing_code`. Rule 5 (AGENTS.md #5): every
    #    row kept, tagged `source_kind`; the read layer resolves redundancy per
    #    practice (#100). `modifier` was added to `prices` after some files were
    #    parsed — union_by_name fills the per-file gap.
    def rate_cols(mod):
        return (
            f"pr.network_name, pr.net, pr.file_id, pr.group_set_id, "
            f"pr.billing_code_type, pr.billing_code, COALESCE({mod}, '') AS modifier, "
            f"pr.setting, pr.service_code, pr.negotiated_type, pr.negotiation_arrangement, "
            f"pr.expiration_date, pr.negotiated_rate, "
            f"CASE WHEN {_scope('pr')} THEN 'outpatient_prof' ELSE 'other' END AS scope, "
            f"pr.negotiated_rate <= COALESCE(cc.ceiling, 1.0) AS is_sentinel, "
            f"{src_kind} AS source_kind, mp.medicare_allowed, "
            f"CASE WHEN mp.medicare_allowed > 0 "
            f"THEN round(pr.negotiated_rate / mp.medicare_allowed, 2) END AS vs_medicare")

    total_rows = 0
    for d in parts:
        slug = d[4:]
        pt = time.time()
        pq = f"read_parquet('{prices_dir}/{d}/*.parquet', hive_partitioning=1, union_by_name=true)"
        have = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {pq}").fetchall()}
        cols = rate_cols("pr.modifier" if "modifier" in have else "CAST(NULL AS VARCHAR)")
        mp_mod = "COALESCE(pr.modifier, '')" if "modifier" in have else "''"
        os.makedirs(f"{rates_out}/net={slug}", exist_ok=True)
        con.execute(f"""
            COPY (
                SELECT {cols}
                FROM {pq} pr
                LEFT JOIN code_ceiling cc
                  ON cc.billing_code_type = pr.billing_code_type AND cc.billing_code = pr.billing_code
                {plan_join}
                LEFT JOIN mpfs mp
                  ON mp.billing_code = pr.billing_code
                 AND mp.billing_code_type = pr.billing_code_type
                 AND mp.modifier = {mp_mod}
            ) TO '{rates_out}/net={slug}/part.parquet' (FORMAT parquet, COMPRESSION zstd)
        """)
        kept = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{rates_out}/net={slug}/part.parquet')").fetchone()[0]
        total_rows += kept
        print(f"  rates net={slug}: {kept:,} rows  {time.time() - pt:.1f}s")

    if not total_rows and not glob.glob(f"{rates_out}/**/*.parquet", recursive=True):
        raise SystemExit("no rates written")
    rates_glob = f"read_parquet('{rates_out}/**/*.parquet', hive_partitioning=1, union_by_name=true)"
    sel_fids = [r[0] for r in con.execute(f"SELECT DISTINCT file_id FROM {rates_glob}").fetchall()]
    fid_list = ", ".join(str(f) for f in sel_fids)

    # ── group_sets — the deduped rosters, for prices -> provider groups -> NPIs
    con.execute(f"""
        COPY (
            SELECT DISTINCT file_id, group_set_id, provider_group_id
            FROM read_parquet('{anthem}/group_sets/*.parquet', union_by_name=true)
            WHERE file_id IN ({fid_list})
        ) TO '{serving_dir}/group_sets.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    # ── group_members — the roster, network-free: (rates ⨝ group_sets ⨝
    #    group_members) expands a price to its NPIs exactly as prices ⨝
    #    group_sets ⨝ providers did.
    providers_src = f"read_parquet('{anthem}/providers/*.parquet', union_by_name=true)"
    con.execute(f"""
        COPY (
            SELECT DISTINCT file_id, provider_group_id, npi, tin_value
            FROM {providers_src}
            WHERE file_id IN ({fid_list})
        ) TO '{serving_dir}/group_members.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    # ── group_networks — which networks a file-local group is attributed to
    #    (the parser's provider_references[].network_name). "This NPI has a rate
    #    in network X" = group_members ⨝ group_networks WHERE net = X — an O(1)
    #    lookup, no rates scan. Slugged in Python so `net` matches the rates
    #    partition key byte-for-byte.
    names = [r[0] for r in con.execute(
        f"SELECT DISTINCT network_name FROM {providers_src} WHERE file_id IN ({fid_list})"
    ).fetchall()]
    con.execute("CREATE TEMP TABLE net_slug (network_name VARCHAR, net VARCHAR)")
    if names:
        con.executemany("INSERT INTO net_slug VALUES (?, ?)",
                        [(nm, network_slug(nm)) for nm in names])
    con.execute(f"""
        COPY (
            SELECT DISTINCT pv.file_id, pv.provider_group_id, ns.net, pv.network_name
            FROM {providers_src} pv
            JOIN net_slug ns ON ns.network_name IS NOT DISTINCT FROM pv.network_name
            WHERE pv.file_id IN ({fid_list})
        ) TO '{serving_dir}/group_networks.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    # ── provider_dim — one row per GA NPPES NPI (the full universe, not scoped
    #    to the built networks). NUCC specialty, DAC identity, geocode, CMS
    #    provider_type, service-line flags left-joined.
    npp_cols = {r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{ga_nppes}')").fetchall()}
    addr = ("g.address_line1, g.address_line2" if "address_line1" in npp_cols
            else "NULL AS address_line1, NULL AS address_line2")
    dac = f"LEFT JOIN read_parquet('{ref_p['dac']}') d ON d.npi = g.npi" if os.path.exists(ref_p["dac"]) else ""
    geo = f"LEFT JOIN read_parquet('{ref_p['geocode']}') gc ON gc.npi = g.npi" if os.path.exists(ref_p["geocode"]) else ""
    nucc = f"LEFT JOIN read_parquet('{ref_p['nucc']}') nx ON nx.taxonomy_code = g.taxonomy_code" if os.path.exists(ref_p["nucc"]) else ""
    ptype = (f"LEFT JOIN (SELECT npi, any_value(provider_type) AS provider_type "
             f"FROM read_parquet('{cms_util}') WHERE provider_type IS NOT NULL GROUP BY npi) "
             f"pt ON pt.npi = g.npi") if os.path.exists(cms_util) else ""
    con.execute(f"""
        COPY (
            SELECT g.npi,
                   COALESCE(NULLIF(g.org_name, ''),
                            NULLIF(trim(BOTH ', ' FROM g.last_name || ', ' || g.first_name), '')) AS name,
                   {"COALESCE(nx.specialty, NULLIF(g.taxonomy_group, 'Other'))" if nucc else "NULLIF(g.taxonomy_group, 'Other')"} AS specialty,
                   {"nx.classification" if nucc else "NULL"} AS nucc_classification,
                   {"nx.grouping" if nucc else "NULL"} AS nucc_grouping,
                   {"pt.provider_type" if ptype else "NULL"} AS cms_provider_type,
                   g.org_name,
                   {"d.org_name" if dac else "NULL"} AS group_name,
                   {"d.org_pac_id" if dac else "NULL"} AS org_pac_id,
                   {"d.grad_year" if dac else "NULL"} AS grad_year,
                   {"gc.latitude" if geo else "NULL"} AS lat,
                   {"gc.longitude" if geo else "NULL"} AS lon,
                   {_service_line_expr("g.taxonomy_code")} AS service_lines,
                   g.is_hospital, g.is_clinic, g.entity_type,
                   g.last_name, g.first_name, g.taxonomy_code, g.taxonomy_group,
                   {addr}, g.city, g.postal_code
            FROM read_parquet('{ga_nppes}') g
            {nucc} {ptype} {dac} {geo}
        ) TO '{serving_dir}/provider_dim.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    # ── provider_affiliations — the DAC hospital CCN<->NPI bridge, verbatim
    #    (already GA-scoped by `make doctors-clinicians`). Empty when not built.
    affil = f"{ref}/dac_hospital_affiliations.parquet"
    if os.path.exists(affil):
        con.execute(f"""
            COPY (
                SELECT DISTINCT npi, ccn, facility_name FROM read_parquet('{affil}')
            ) TO '{serving_dir}/provider_affiliations.parquet' (FORMAT parquet, COMPRESSION zstd)
        """)
    else:
        con.execute("CREATE TEMP TABLE _af (npi BIGINT, ccn VARCHAR, facility_name VARCHAR)")
        con.execute(f"COPY _af TO '{serving_dir}/provider_affiliations.parquet' "
                    f"(FORMAT parquet, COMPRESSION zstd)")

    # ── code_dim — one row per code in `rates`. `shoppable` = a plain CPT
    #    procedure code (excludes HCPCS Level II incl. J/Q drugs, RC, MS-DRG,
    #    APC, ICD, CDT — #51).
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
                   {"cl.rbcs_subcategory" if labels else "NULL"} AS rbcs_subcategory,
                   {"cl.rbcs_family" if labels else "NULL"} AS rbcs_family,
                   {"cl.rbcs_is_major" if labels else "NULL"} AS rbcs_is_major,
                   {"cl.search_text" if labels else "NULL"} AS search_text,
                   (u.billing_code_type = 'CPT'
                    AND regexp_full_match(u.billing_code, '[0-9]{{5}}')) AS shoppable,
                   mg.medicare_allowed
            FROM used u
            {labels}
            LEFT JOIN nm USING (billing_code_type, billing_code)
            LEFT JOIN mg USING (billing_code, billing_code_type)
        ) TO '{serving_dir}/code_dim.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    # ── evidence: (npi, billing_code, tier, ...) for NPIs reachable through a
    #    rate. billed (this NPI billed it to Medicare Part B) wins over typical
    #    (>= TYPICAL_THRESHOLD of the NPI's NUCC classification bill it). A
    #    billed row carries the utilization detail /rates/quote shows
    #    (serving/evidence.did_bill's aggregation, over place-of-service); a
    #    typical row carries the classification's prevalence so the read layer
    #    can tighten the threshold per request.
    gm = f"read_parquet('{serving_dir}/group_members.parquet')"
    ev_cols = ("npi, billing_code, tier, prevalence, year, tot_srvcs, tot_benes, "
               "tot_bene_days, avg_mdcr_allowed, is_drug")
    ev_parts = []
    if os.path.exists(cms_util):
        ev_parts.append(f"""
            SELECT npi, hcpcs_cd AS billing_code, 'billed' AS tier,
                   NULL::DOUBLE AS prevalence,
                   max(year) AS year,
                   sum(tot_srvcs)::BIGINT AS tot_srvcs,
                   sum(tot_benes)::BIGINT AS tot_benes,
                   sum(tot_bene_day_srvcs)::BIGINT AS tot_bene_days,
                   round(sum(avg_mdcr_alowd_amt * tot_srvcs) / nullif(sum(tot_srvcs), 0), 2)
                       AS avg_mdcr_allowed,
                   bool_or(hcpcs_drug_ind = 'Y') AS is_drug
            FROM read_parquet('{cms_util}')
            WHERE npi IN (SELECT npi FROM {gm})
            GROUP BY 1, 2
        """)
    if os.path.exists(cms_util) and os.path.exists(ref_p["profiles"]) and os.path.exists(ref_p["nucc"]):
        ev_parts.append(f"""
            SELECT s.npi, sp.hcpcs_cd AS billing_code, 'typical' AS tier,
                   max(sp.prevalence) AS prevalence,
                   NULL::INTEGER, NULL::BIGINT, NULL::BIGINT, NULL::BIGINT,
                   NULL::DOUBLE, NULL::BOOLEAN
            FROM (
                SELECT DISTINCT g.npi, nx.classification
                FROM read_parquet('{ga_nppes}') g
                JOIN read_parquet('{ref_p["nucc"]}') nx ON nx.taxonomy_code = g.taxonomy_code
                WHERE g.npi IN (SELECT npi FROM {gm})
            ) s
            JOIN read_parquet('{ref_p["profiles"]}') sp ON sp.specialty = s.classification
            WHERE sp.prevalence >= {TYPICAL_THRESHOLD}
              AND NOT EXISTS (
                SELECT 1 FROM read_parquet('{cms_util}') b
                WHERE b.npi = s.npi AND b.hcpcs_cd = sp.hcpcs_cd)
            GROUP BY 1, 2
        """)
    if ev_parts:
        con.execute(f"COPY (SELECT {ev_cols} FROM ({' UNION ALL '.join(ev_parts)})) "
                    f"TO '{serving_dir}/evidence.parquet' (FORMAT parquet, COMPRESSION zstd)")
    else:
        con.execute("CREATE TEMP TABLE _ev (npi BIGINT, billing_code VARCHAR, tier VARCHAR, "
                    "prevalence DOUBLE, year INTEGER, tot_srvcs BIGINT, tot_benes BIGINT, "
                    "tot_bene_days BIGINT, avg_mdcr_allowed DOUBLE, is_drug BOOLEAN)")
        con.execute(f"COPY _ev TO '{serving_dir}/evidence.parquet' (FORMAT parquet, COMPRESSION zstd)")

    # ── rate_hist — the browse primitive. A $25 bucketed histogram per
    #    (net, code, setting, scope, modifier) whose `n` is roster-weighted:
    #    each price row contributes its group_set's provider-group count, so
    #    `SUM(n)` over a slice approximates the fanned-out (group × rate) volume
    #    without materialising it; `n_rates` is the raw price-row count. The
    #    network overview rolls modifiers up; /rates/by_network filters to the
    #    global (`''`) rate. p10/median/p90 come off the CDF at serve time.
    #    `is_sentinel` is a dimension so the histogram can still show the
    #    placeholder rows (known-gaps) while the rollup below excludes them.
    #    Replaces summary/{rate_hist,rate_summary,code_rollup}.
    con.execute(f"""
        COPY (
            WITH roster AS (
                SELECT file_id, group_set_id, COUNT(*)::BIGINT AS n_groups
                FROM read_parquet('{serving_dir}/group_sets.parquet') GROUP BY 1, 2
            )
            SELECT 'anthem' AS payer, rt.net, rt.network_name,
                   rt.billing_code_type, rt.billing_code, rt.setting, rt.scope, rt.modifier,
                   rt.is_sentinel,
                   CASE WHEN rt.negotiated_rate >= {HIST_CAP} THEN {HIST_CAP}
                        WHEN rt.negotiated_rate < 0 THEN 0
                        ELSE floor(rt.negotiated_rate / {HIST_WIDTH}) * {HIST_WIDTH}
                   END AS bucket,
                   SUM(roster.n_groups)::BIGINT AS n,
                   COUNT(*)::BIGINT AS n_rates
            FROM {rates_glob} rt
            JOIN roster USING (file_id, group_set_id)
            -- network_name, not just net: two distinct Anthem network names
            -- ("HMO PAR NETWORK (NON-ATL MBR SEEKS CARE IN ATL)" vs the same
            -- text minus punctuation) can slugify to the same `net` partition
            -- key. Grouping by net alone silently blended their rates into
            -- one row (caught by #96's by_network golden test dropping a
            -- network); network_name keeps them distinct rows that happen to
            -- share a partition, same as `group_networks`.
            GROUP BY rt.net, rt.network_name, rt.billing_code_type, rt.billing_code,
                     rt.setting, rt.scope, rt.modifier, rt.is_sentinel, bucket
        ) TO '{serving_dir}/rate_hist.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    # ── cross_network_rollup — (code, network) -> roster-weighted volume +
    #    min/p10/median/p90/max, read straight by /rates/by_network. Derived
    #    from the rate_hist CDF (global, outpatient-prof, non-sentinel,
    #    non-overflow buckets); a percentile is the bucket where the cumulative
    #    weight crosses the threshold, at its midpoint; min/max are the bucket
    #    edges. Not a raw-price scan. Keyed on (net, network_name), not net
    #    alone — see the rate_hist comment above.
    mid = HIST_WIDTH / 2.0
    con.execute(f"""
        COPY (
            WITH h AS (
                SELECT billing_code, billing_code_type, net, network_name,
                       bucket, SUM(n) AS n
                FROM read_parquet('{serving_dir}/rate_hist.parquet')
                WHERE scope = 'outpatient_prof' AND modifier = '' AND NOT is_sentinel
                  AND bucket < {HIST_CAP}
                GROUP BY billing_code, billing_code_type, net, network_name, bucket
            ),
            cum AS (
                SELECT *, SUM(n) OVER w AS c, SUM(n) OVER p AS tot
                FROM h
                WINDOW p AS (PARTITION BY billing_code, billing_code_type, net, network_name),
                       w AS (PARTITION BY billing_code, billing_code_type, net, network_name
                             ORDER BY bucket)
            )
            SELECT billing_code, billing_code_type, net, network_name,
                   any_value(tot) AS n_groups,
                   round(MIN(bucket), 2) AS min_rate,
                   round(MIN(bucket) FILTER (WHERE c >= 0.1 * tot) + {mid}, 2) AS p10,
                   round(MIN(bucket) FILTER (WHERE c >= 0.5 * tot) + {mid}, 2) AS median,
                   round(MIN(bucket) FILTER (WHERE c >= 0.9 * tot) + {mid}, 2) AS p90,
                   round(MAX(bucket) + {HIST_WIDTH}, 2) AS max_rate
            FROM cum GROUP BY 1, 2, 3, 4
        ) TO '{serving_dir}/cross_network_rollup.parquet' (FORMAT parquet, COMPRESSION zstd)
    """)

    def n(name):
        return con.execute(f"SELECT COUNT(*) FROM read_parquet('{serving_dir}/{name}')").fetchone()[0]

    counts = {"rates": total_rows}
    print(f"\n  rates                  {total_rows:>13,}")
    for f in TABLES:
        counts[f[:-len(".parquet")]] = n(f)
        print(f"  {f:<22} {counts[f[:-len('.parquet')]]:>13,}")
    print(f"\n  total {time.time() - t0:.1f}s -> {serving_dir}/")
    con.close()
    if os.getenv("DUCKDB_TMP") is None:
        shutil.rmtree(spill, ignore_errors=True)

    # ── manifest — what this build was made from. GET / reports it: the API
    #    no longer looks at data/{nppes,reference,cms} itself, so "is NPPES
    #    loaded" means "was it present when the serving tables were built".
    manifest = {
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "networks": [d[4:] for d in parts],
        "partial": networks is not None,
        "inputs": {
            "nppes": os.path.exists(ga_nppes),
            "nucc": os.path.exists(ref_p["nucc"]),
            "code_labels": os.path.exists(ref_p["labels"]),
            "cms_utilization": os.path.exists(cms_util),
            "specialty_profiles": os.path.exists(ref_p["profiles"]),
            "mpfs": os.path.exists(ref_p["mpfs"]),
            "dac": os.path.exists(ref_p["dac"]),
            "dac_hospital_affiliations": os.path.exists(affil),
            "geocode": os.path.exists(ref_p["geocode"]),
            "plan_link": have_plan_link,
        },
        "rows": counts,
        "hist_width": HIST_WIDTH, "hist_cap": HIST_CAP,
        "typical_threshold": TYPICAL_THRESHOLD,
    }
    with open(f"{serving_dir}/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)


# Every table a complete build writes (besides the rates/ partition tree).
TABLES = ("group_sets.parquet", "group_members.parquet", "group_networks.parquet",
          "provider_dim.parquet", "provider_affiliations.parquet", "code_dim.parquet",
          "evidence.parquet", "rate_hist.parquet", "cross_network_rollup.parquet")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR", "/app/data"))
    ap.add_argument("--serving-dir", default=None,
                    help="output dir (default: SERVING_DIR env, else <data-dir>/serving)")
    ap.add_argument("--networks", default=None,
                    help="comma-separated net slugs to build (default: all partitions)")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    data_dir = "data-test" if args.test else args.data_dir
    serving_dir = args.serving_dir or _serving_dir(data_dir, args.test)
    networks = [s.strip() for s in args.networks.split(",")] if args.networks else None

    if not os.path.isdir(f"{store_dir(data_dir, args.test, 'anthem', 'ANTHEM_DIR')}/prices"):
        raise SystemExit("no rate store — run `make parse` first")
    build(data_dir, serving_dir, networks, args.test)


if __name__ == "__main__":
    main()
