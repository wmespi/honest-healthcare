"""Where the data lives and how to query it.

The serving layer reads Parquet (see ../docs/schema.md); Postgres holds only the
discovery queue. This module centralises the glob paths, the DuckDB connection
factory, and the "is there any data yet" guards that every router needs.
"""
import glob as _glob
import os
import re
from typing import Optional

DATA_DIR = os.getenv("DATA_DIR", "/app/data")

# Normalized rate store:
#   prices/net=<slug>/<id>.parquet  — one row per (network × negotiated price),
#     Hive-partitioned by network; carries file_id + group_set_id.
#   group_sets/<id>.parquet         — file_id | group_set_id | provider_group_id,
#     the deduplicated provider-group rosters. Join prices → group_sets on
#     (file_id, group_set_id) to expand a price to its provider groups.
PRICES_GLOB     = f"{DATA_DIR}/anthem/prices/**/*.parquet"
PRICES_SRC      = f"read_parquet('{PRICES_GLOB}', union_by_name=true, hive_partitioning=1)"
GROUP_SETS_GLOB = f"{DATA_DIR}/anthem/group_sets/*.parquet"
GROUP_SETS_SRC  = f"read_parquet('{GROUP_SETS_GLOB}', union_by_name=true)"
PROVIDERS_GLOB  = f"{DATA_DIR}/anthem/providers/*.parquet"
PROVIDERS_SRC   = f"read_parquet('{PROVIDERS_GLOB}', union_by_name=true)"

CODES_GLOB       = f"{DATA_DIR}/anthem/codes/*.parquet"
NPI_LOOKUP_PATH  = f"{DATA_DIR}/anthem/npi_lookup.parquet"

# Browse-layer summary (scripts/build_rate_summary.py — `make build-summary`).
# The /networks · /billing_codes · /procedure_categories endpoints read these
# instead of scanning prices ⨝ group_sets (~1e9 rows). Absent until the build
# runs; the endpoints fall back to the live scan (VOL_CTE) when so.
RATE_SUMMARY_PATH = f"{DATA_DIR}/anthem/summary/rate_summary.parquet"
RATE_HIST_PATH    = f"{DATA_DIR}/anthem/summary/rate_hist.parquet"
CODE_ROLLUP_PATH  = f"{DATA_DIR}/anthem/summary/code_rollup.parquet"
GA_NPPES_PATH    = f"{DATA_DIR}/nppes/ga_providers.parquet"
CODE_LABELS_PATH = f"{DATA_DIR}/reference/code_labels.parquet"
NUCC_PATH        = f"{DATA_DIR}/reference/nucc_taxonomy.parquet"

# CMS "Medicare Physician & Other Practitioners — by Provider and Service":
# one row per (GA NPI × HCPCS × place-of-service) actually billed to Medicare
# Part B. The evidence layer behind did_bill() — see serving/evidence.py and
# reference/cms-utilization.md. Absent until `make cms-utilization` runs.
CMS_UTILIZATION_PATH = f"{DATA_DIR}/cms/ga_provider_service.parquet"

# CMS Medicare Physician Fee Schedule allowed amount per (code × modifier ×
# facility/non-facility × Georgia locality) — the per-code benchmark / fallback
# price behind serving/benchmark.py and /rates/quote's `medicare_allowed`.
# Absent until `make mpfs` runs. See reference/mpfs.md.
MPFS_GA_PATH = f"{DATA_DIR}/reference/mpfs_ga.parquet"

# Per-specialty procedure prevalence, learned from CMS_UTILIZATION_PATH ∩ NPPES ∩
# NUCC. Tier 2 of the provider↔procedure story: "typical for this specialty" when
# there's no direct utilization row. Absent until `make specialty-profiles` runs.
SPECIALTY_PROFILES_PATH = f"{DATA_DIR}/reference/specialty_procedure_profiles.parquet"

# prices expanded to one row per provider group — the common join. A billing_code
# / net filter on the outer query prunes `prices` before the join runs.
PRICE_GROUPS_SRC = f"""(
    SELECT p.*, m.provider_group_id
    FROM {PRICES_SRC} p
    JOIN {GROUP_SETS_SRC} m
      ON m.file_id = p.file_id AND m.group_set_id = p.group_set_id
)"""

# per-code provider-group volume — the browse-layer ranking hint behind
# /billing_codes and /procedure_categories. Avoids a COUNT(DISTINCT group) over
# the full prices ⨝ group_sets expansion: it sizes each roster once (tiny), then
# sums roster sizes over each code's distinct rosters. That over-counts a group
# that sits in several of a code's rosters — fine for a ranking hint, and a
# precomputed exact summary replaces it in issue #10.
VOL_CTE = f"""
    WITH set_size AS (
        SELECT file_id, group_set_id, COUNT(*) AS n
        FROM {GROUP_SETS_SRC}
        GROUP BY 1, 2
    ),
    code_sets AS (
        SELECT DISTINCT billing_code, billing_code_type, file_id, group_set_id
        FROM {PRICES_SRC}
    )
    SELECT cs.billing_code, cs.billing_code_type,
           SUM(ss.n) AS provider_groups
    FROM code_sets cs
    JOIN set_size ss USING (file_id, group_set_id)
    GROUP BY 1, 2
"""

_DUCK_TMP = os.getenv("DUCKDB_TMP", "/tmp/duckdb_spill")
_DUCK_MEM = os.getenv("DUCKDB_MEMORY_LIMIT", "4GB")


def network_slug(name: str) -> str:
    """Partition key for a network_name. MUST match etl/extraction/partition.go:slugifyNetwork."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    s = s[:100].strip("-")
    return s or "_unattributed"


def outpatient_scope(alias: str = "pg") -> str:
    """The slice the consumer rate views compare: outpatient professional
    fee-for-service dollar amounts.

    Drops institutional/facility lines, inpatient-only rates, `bundle` /
    `capitation` arrangements, and `percentage` / `per diem` / `derived` types —
    whose `negotiated_rate` is not a per-visit dollar figure (a "60.0" means 60%
    of billed charges, not $60). `setting = 'both'` stays: it applies in either
    setting, outpatient included. Inpatient and the other types remain in the
    store for a later dedicated view — see docs/known-gaps.md.
    """
    a = f"{alias}." if alias else ""
    return (
        f"{a}billing_class = 'professional' "
        f"AND {a}setting IN ('outpatient', 'both') "
        f"AND {a}negotiation_arrangement = 'ffs' "
        f"AND {a}negotiated_type IN ('fee schedule', 'negotiated')"
    )


def price_filters(billing_code, billing_code_type, network_name, setting, npi,
                  specialty=None, scope=True):
    """Shared WHERE for the price_groups source (alias pg). Returns (sql, params).

    `specialty` (a NUCC classification/specialization label) keeps only groups
    that contain at least one provider of that specialty — a coarse but useful
    scope ("cardiologists' contracted rates for an echo"). Cheap when a
    billing_code is also given (prices prune to one code first).

    `scope=True` appends `outpatient_scope()` — the default for every consumer
    rate view. Pass `scope=False` only for a view that deliberately spans
    settings / arrangements.
    """
    conditions = ["1=1"]
    params: list = []
    if scope:
        conditions.append(outpatient_scope("pg"))
    if billing_code:
        conditions += ["pg.billing_code = ?", "pg.billing_code_type = ?"]
        params += [billing_code, billing_code_type]
    if network_name:
        # net is the Hive partition key — prunes the scan to one directory.
        conditions.append("pg.net = ?")
        params.append(network_slug(network_name))
    if scope and setting not in (None, "", "outpatient", "both"):
        # `inpatient` / `ancillary` can't narrow an outpatient-scoped view —
        # ignore rather than return an empty screen (the inpatient view is #TBD).
        setting = None
    if setting:
        conditions.append("pg.setting = ?")
        params.append(setting)
    if npi:
        conditions.append(f"""EXISTS (
            SELECT 1 FROM {PROVIDERS_SRC} pv
            WHERE pv.file_id = pg.file_id
              AND pv.provider_group_id = pg.provider_group_id
              AND pv.npi = ?)""")
        params.append(npi)
    if specialty and os.path.exists(GA_NPPES_PATH) and os.path.exists(NUCC_PATH):
        conditions.append(f"""EXISTS (
            SELECT 1 FROM {PROVIDERS_SRC} pv
            JOIN read_parquet('{GA_NPPES_PATH}') n ON n.npi = pv.npi
            JOIN read_parquet('{NUCC_PATH}') x ON x.taxonomy_code = n.taxonomy_code
            WHERE pv.file_id = pg.file_id
              AND pv.provider_group_id = pg.provider_group_id
              AND (x.specialty ILIKE ? OR x.classification ILIKE ?))""")
        params += [f"%{specialty}%", f"%{specialty}%"]
    return " AND ".join(conditions), params


def db():
    """A fresh DuckDB connection. Bounds memory and lets big aggregates spill to
    disk instead of OOM-killing the process. A persistent pooled connection + a
    precomputed browse-layer summary are the next step (issue #10) — until then
    the browse endpoints (/networks aside) full-scan prices ⨝ group_sets."""
    import duckdb
    conn = duckdb.connect()
    try:
        os.makedirs(_DUCK_TMP, exist_ok=True)
        conn.execute(f"SET memory_limit = '{_DUCK_MEM}'")
        conn.execute(f"SET temp_directory = '{_DUCK_TMP}'")
        conn.execute("SET preserve_insertion_order = false")
    except Exception:
        pass
    return conn


def has_parquet(glob_dir: str) -> bool:
    """Cheap check for whether any parquet has been written under a data subtree
    (a bare read_parquet over an empty glob raises)."""
    return bool(_glob.glob(glob_dir, recursive=True))


def have_prices() -> bool:
    return has_parquet(PRICES_GLOB)


def have_summary() -> bool:
    """The browse-layer rollups have been built (`make build-summary`) — what
    `/networks`, `/billing_codes`, `/procedure_categories` read."""
    return os.path.exists(RATE_SUMMARY_PATH) and os.path.exists(CODE_ROLLUP_PATH)


def have_rate_hist() -> bool:
    """The pre-bucketed histogram is present — gates the no-code network
    overview (`/rates/distribution`) onto the summary path. Written by the same
    build; split out so an older summary without it doesn't also knock the
    rollup endpoints off their fast path."""
    return os.path.exists(RATE_HIST_PATH)


_NPPES_COLS: Optional[set] = None


def nppes_cols(conn) -> set:
    """Column names in the NPPES GA parquet (cached) — lets endpoints reference
    address_line1/2 only after a re-extract that added them."""
    global _NPPES_COLS
    if _NPPES_COLS is None:
        try:
            _NPPES_COLS = {
                r[0] for r in conn.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{GA_NPPES_PATH}')").fetchall()
            }
        except Exception:
            _NPPES_COLS = set()
    return _NPPES_COLS
