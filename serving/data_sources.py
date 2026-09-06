"""Where the serving tables live and how to query them.

The API reads ONLY `data/serving/` — the build step's output (`make build`,
build/build.py; schema in ../docs/schema.md). Raw `anthem/`, `nppes/`,
`reference/`, `cms/` are build inputs, never read here, and there is no
fallback when the build is missing: `have_build()` is false, `GET /` says so,
and every other route errors. This module holds the table sources, the one
process-wide DuckDB database, `network_slug()` and the shared rate filter.
"""
import glob as _glob
import json
import os
import re
import threading
from typing import Optional

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
# The build's output dir. SERVING_DIR lets a worktree serve its own
# ./data-local/serving while the build reads the shared corpus (GH #59); with
# nothing set this is DATA_DIR/serving. `or`, not the getenv default: Compose
# passes an unset var through as "".
SERVING_DIR = os.getenv("SERVING_DIR") or f"{DATA_DIR}/serving"

# ── the serving tables ─────────────────────────────────────────────────────
#
# rates/net=<slug>/part.parquet  one row per negotiated price (the parser's
#     grain) + scope / is_sentinel / source_kind / medicare_allowed. Hive-
#     partitioned by network: a `net = ?` filter prunes to one directory, and
#     that plus `billing_code = ?` is what makes every plan-scoped query cheap.
# group_sets        (file_id, group_set_id, provider_group_id)  a price's roster
# group_members     (file_id, provider_group_id, npi, tin_value)  a group's NPIs
# group_networks    (file_id, provider_group_id, net, network_name)
# provider_dim      one row per GA NPPES NPI, enriched
# provider_affiliations  (npi, ccn, facility_name)
# code_dim          one row per priced code: label, category, benchmark
# evidence          (npi, billing_code, tier, ...)  billed | typical
# rate_hist         roster-weighted $25 histogram per (net, code, setting,
#                   scope, modifier, is_sentinel) — every browse view
# cross_network_rollup   (code, net) -> n_groups, min/p10/median/p90/max
RATES_GLOB = f"{SERVING_DIR}/rates/**/*.parquet"
RATES_SRC = f"read_parquet('{RATES_GLOB}', hive_partitioning=1, union_by_name=true)"


def _table(name: str) -> str:
    return f"read_parquet('{SERVING_DIR}/{name}.parquet')"


GROUP_SETS_SRC = _table("group_sets")
GROUP_MEMBERS_SRC = _table("group_members")
GROUP_NETWORKS_SRC = _table("group_networks")
PROVIDER_DIM_SRC = _table("provider_dim")
PROVIDER_AFFIL_SRC = _table("provider_affiliations")
CODE_DIM_SRC = _table("code_dim")
EVIDENCE_SRC = _table("evidence")
RATE_HIST_SRC = _table("rate_hist")
ROLLUP_SRC = _table("cross_network_rollup")
MANIFEST_PATH = f"{SERVING_DIR}/manifest.json"

# A price expanded to its provider groups — the common join. A `net` /
# `billing_code` filter on the outer query prunes `rates` before the join runs.
RATE_GROUPS_SRC = f"""(
    SELECT r.*, gs.provider_group_id
    FROM {RATES_SRC} r
    JOIN {GROUP_SETS_SRC} gs
      ON gs.file_id = r.file_id AND gs.group_set_id = r.group_set_id
)"""

_TABLES = ("group_sets", "group_members", "group_networks", "provider_dim",
           "provider_affiliations", "code_dim", "evidence", "rate_hist",
           "cross_network_rollup")


def missing_build() -> list:
    """Which serving tables are absent — [] when the build is complete."""
    out = [t for t in _TABLES if not os.path.exists(f"{SERVING_DIR}/{t}.parquet")]
    if not _glob.glob(RATES_GLOB, recursive=True):
        out.insert(0, "rates")
    if not os.path.exists(MANIFEST_PATH):
        out.append("manifest.json")
    return out


def have_build() -> bool:
    return not missing_build()


_MANIFEST: Optional[tuple] = None   # (mtime, dict)


def manifest() -> dict:
    """The build's manifest.json (what it was built from, when, row counts).
    Cached on the file's mtime so a rebuild is picked up without a restart.
    {} when there is no build."""
    global _MANIFEST
    try:
        mt = os.path.getmtime(MANIFEST_PATH)
    except OSError:
        return {}
    if _MANIFEST is None or _MANIFEST[0] != mt:
        try:
            with open(MANIFEST_PATH) as f:
                _MANIFEST = (mt, json.load(f))
        except (OSError, ValueError):
            return {}
    return _MANIFEST[1]


def built_with(input_name: str) -> bool:
    """Whether a build input (nppes, nucc, cms_utilization, mpfs, dac, ...) was
    present when the serving tables were built — the only sense in which a
    reference dataset is "loaded" now that the API never reads it directly."""
    return bool(manifest().get("inputs", {}).get(input_name))


_DUCK_TMP = os.getenv("DUCKDB_TMP", "/tmp/duckdb_spill")
_DUCK_MEM = os.getenv("DUCKDB_MEMORY_LIMIT", "4GB")
_DUCK_THREADS = os.getenv("DUCKDB_THREADS")
_CONN = None
_CONN_LOCK = threading.Lock()


def db():
    """A cursor on the one process-wide DuckDB database.

    The database is created once (memory limit, spill dir, thread count set
    there, once) and kept for the life of the process, so Parquet metadata and
    zonemaps stay cached across requests. Each call returns `cursor()` — a
    lightweight connection to that same database with its own transaction and
    temp-table namespace, which is what makes it safe to use from FastAPI's
    request threads concurrently. Never `duckdb.connect()` per request.
    """
    global _CONN
    if _CONN is None:
        with _CONN_LOCK:
            if _CONN is None:
                import duckdb
                conn = duckdb.connect()
                try:
                    os.makedirs(_DUCK_TMP, exist_ok=True)
                    conn.execute(f"SET memory_limit = '{_DUCK_MEM}'")
                    conn.execute(f"SET temp_directory = '{_DUCK_TMP}'")
                    if _DUCK_THREADS:
                        conn.execute(f"SET threads = {int(_DUCK_THREADS)}")
                    conn.execute("SET preserve_insertion_order = false")
                except Exception:
                    pass
                _CONN = conn
    return _CONN.cursor()


def network_slug(name: str) -> str:
    """Partition key for a network_name. MUST match etl/extraction/partition.go:slugifyNetwork."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    s = s[:100].strip("-")
    return s or "_unattributed"


def outpatient_scope(alias: str = "pg") -> str:
    """The slice the consumer rate views compare: outpatient professional
    fee-for-service dollar amounts. The build bakes this into `rates.scope =
    'outpatient_prof'` (build/build.py's OUTPATIENT_PROF; test_build.py pins the
    two strings equal) — routes filter on the column, this is the definition.

    Drops institutional/facility lines, inpatient-only rates, `bundle` /
    `capitation` arrangements, and `percentage` / `per diem` / `derived` types —
    whose `negotiated_rate` is not a per-visit dollar figure (a "60.0" means 60%
    of billed charges, not $60). `setting = 'both'` stays: it applies in either
    setting, outpatient included. See docs/known-gaps.md.
    """
    a = f"{alias}." if alias else ""
    return (
        f"{a}billing_class = 'professional' "
        f"AND {a}setting IN ('outpatient', 'both') "
        f"AND {a}negotiation_arrangement = 'ffs' "
        f"AND {a}negotiated_type IN ('fee schedule', 'negotiated')"
    )


def rate_filters(billing_code, billing_code_type, network_name, setting, npi,
                 specialty=None, scope=True, drop_sentinel=False):
    """Shared WHERE for the RATE_GROUPS_SRC source (alias pg). Returns (sql, params).

    `scope=True` keeps `scope = 'outpatient_prof'` — the default for every
    consumer rate view. `drop_sentinel=True` removes the placeholder rows
    (`is_sentinel`; jobs 1-3 do, the histogram doesn't). `npi=` keeps groups
    containing that NPI; `specialty=` (a NUCC label) keeps groups containing at
    least one provider of that specialty — coarse but useful, and cheap once a
    `billing_code` has pruned `rates`.
    """
    conditions = ["1=1"]
    params: list = []
    if scope:
        conditions.append("pg.scope = 'outpatient_prof'")
    if drop_sentinel:
        conditions.append("NOT pg.is_sentinel")
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
            SELECT 1 FROM {GROUP_MEMBERS_SRC} gm
            WHERE gm.file_id = pg.file_id
              AND gm.provider_group_id = pg.provider_group_id
              AND gm.npi = ?)""")
        params.append(npi)
    if specialty:
        conditions.append(f"""EXISTS (
            SELECT 1 FROM {GROUP_MEMBERS_SRC} gm
            JOIN {PROVIDER_DIM_SRC} pd ON pd.npi = gm.npi
            WHERE gm.file_id = pg.file_id
              AND gm.provider_group_id = pg.provider_group_id
              AND (pd.specialty ILIKE ? OR pd.nucc_classification ILIKE ?))""")
        params += [f"%{specialty}%", f"%{specialty}%"]
    return " AND ".join(conditions), params
