import os
import re
import duckdb
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

DATA_DIR        = os.getenv("DATA_DIR", "/app/data")
# Normalized rate store (see AGENTS.md → Parquet Schema):
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

CODES_GLOB      = f"{DATA_DIR}/anthem/codes/*.parquet"
NPI_LOOKUP_PATH = f"{DATA_DIR}/anthem/npi_lookup.parquet"
GA_NPPES_PATH   = f"{DATA_DIR}/nppes/ga_providers.parquet"
CODE_LABELS_PATH = f"{DATA_DIR}/reference/code_labels.parquet"


def network_slug(name: str) -> str:
    """Partition key for a network_name. MUST match etl-go/partition.go:slugifyNetwork."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    s = s[:100].strip("-")
    return s or "_unattributed"

app = FastAPI(title="Honest Healthcare API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_DUCK_TMP = os.getenv("DUCKDB_TMP", "/tmp/duckdb_spill")
_DUCK_MEM = os.getenv("DUCKDB_MEMORY_LIMIT", "4GB")


def db():
    conn = duckdb.connect()
    # Bound memory and let big aggregates spill to disk instead of OOM-killing
    # the process. A persistent pooled connection + a precomputed browse-layer
    # summary are the next step (issue #10) — until then the browse endpoints
    # (/networks aside) full-scan prices ⨝ group_sets.
    try:
        os.makedirs(_DUCK_TMP, exist_ok=True)
        conn.execute(f"SET memory_limit = '{_DUCK_MEM}'")
        conn.execute(f"SET temp_directory = '{_DUCK_TMP}'")
        conn.execute("SET preserve_insertion_order = false")
    except Exception:
        pass
    return conn


def _has_parquet(glob_dir: str) -> bool:
    """Cheap check for whether any parquet has been written under a data subtree
    (a bare read_parquet over an empty glob raises)."""
    import glob as _g
    return bool(_g.glob(glob_dir, recursive=True))


def have_prices() -> bool:
    return _has_parquet(PRICES_GLOB)


@app.get("/")
def health():
    conn = db()
    try:
        prices    = conn.execute(f"SELECT COUNT(*) FROM {PRICES_SRC}").fetchone()[0]
        edges     = conn.execute(f"SELECT COUNT(*) FROM {GROUP_SETS_SRC}").fetchone()[0]
        providers = conn.execute(f"SELECT COUNT(*) FROM {PROVIDERS_SRC}").fetchone()[0]
        return {"status": "ok", "total_prices": prices,
                "total_group_set_edges": edges, "total_providers": providers}
    except Exception as e:
        return {"status": "ok", "note": str(e)}


def _price_filters(billing_code, billing_code_type, network_name, setting, npi):
    """Shared WHERE for the price_groups source (alias pg). Returns (sql, params)."""
    conditions = ["1=1"]
    params: list = []
    if billing_code:
        conditions += ["pg.billing_code = ?", "pg.billing_code_type = ?"]
        params += [billing_code, billing_code_type]
    if network_name:
        # net is the Hive partition key — prunes the scan to one directory.
        conditions.append("pg.net = ?")
        params.append(network_slug(network_name))
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
    return " AND ".join(conditions), params


@app.get("/rates/distribution")
def rate_distribution(
    billing_code: Optional[str] = None,
    billing_code_type: str = "CPT",
    plan_name: Optional[str] = None,  # accepted for API compat, unused
    network_name: Optional[str] = None,
    setting: Optional[str] = None,
    npi: Optional[int] = None,
):
    """
    Rate distribution. When billing_code is omitted, returns a network-wide overview
    (pre-bucketed at the SQL level to keep the response size manageable).
    """
    conn = db()

    # Expanding prices → provider groups is only affordable when the filter
    # prunes prices hard (a billing_code) or the query needs per-NPI resolution
    # (an npi filter). The bare overview (no code, maybe a network) aggregates
    # over `prices` alone — bars/counts are distinct provider *rosters*, not the
    # fully-expanded group count.
    heavy = bool(billing_code or npi)

    if heavy:
        where, params = _price_filters(billing_code, billing_code_type, network_name, setting, npi)
        src = f"{PRICE_GROUPS_SRC} pg"
        grp = "COUNT(DISTINCT (pg.file_id, pg.provider_group_id))"
    else:
        # prices-only: reuse _price_filters minus the npi branch (npi ⇒ heavy).
        where, params = _price_filters(None, billing_code_type, network_name, setting, None)
        src = f"{PRICES_SRC} pg"
        grp = "COUNT(DISTINCT pg.group_set_id)"

    if not billing_code:
        dist = conn.execute(f"""
            SELECT
                FLOOR(LEAST(pg.negotiated_rate, 2000) / 50) * 50 AS rate,
                'fee schedule' AS negotiated_type,
                {grp} AS provider_groups
            FROM {src}
            WHERE {where}
            GROUP BY 1, 2
            ORDER BY 1
        """, params).fetchall()
    else:
        dist = conn.execute(f"""
            SELECT
                pg.negotiated_rate,
                pg.negotiated_type,
                {grp} AS provider_groups
            FROM {src}
            WHERE {where}
            GROUP BY pg.negotiated_rate, pg.negotiated_type
            ORDER BY pg.negotiated_rate
        """, params).fetchall()

    if not dist:
        label = f"{billing_code_type}:{billing_code}" if billing_code else "network"
        raise HTTPException(404, detail=f"No rates found for {label}")

    stats = conn.execute(f"""
        SELECT
            MIN(pg.negotiated_rate),
            MAX(pg.negotiated_rate),
            AVG(pg.negotiated_rate),
            MEDIAN(pg.negotiated_rate),
            {grp},
            COUNT(*)
        FROM {src}
        WHERE {where}
    """, params).fetchone()

    n_providers = None
    if heavy:
        n_providers = conn.execute(f"""
            SELECT COUNT(DISTINCT pv.npi)
            FROM {src}
            JOIN {PROVIDERS_SRC} pv
              ON pv.file_id = pg.file_id AND pv.provider_group_id = pg.provider_group_id
            WHERE {where}
        """, params).fetchone()[0]

    return {
        "billing_code":      billing_code or "ALL",
        "billing_code_type": billing_code_type if billing_code else "NETWORK",
        "summary": {
            "min":             round(stats[0], 2),
            "max":             round(stats[1], 2),
            "avg":             round(stats[2], 2),
            "median":          round(stats[3], 2),
            "provider_groups": stats[4],
            "n_providers":     n_providers,
            "total_entries":   stats[5],
        },
        "distribution": [
            {"rate": r[0], "type": r[1], "provider_groups": r[2]}
            for r in dist
        ],
    }


@app.get("/rates/providers")
def rates_by_provider(
    billing_code: str,
    billing_code_type: str = "CPT",
    plan_name: Optional[str] = None,
    network_name: Optional[str] = None,
    setting: Optional[str] = None,
    npi: Optional[int] = None,
    ga_hospitals_only: bool = False,
    sort: str = "rate_asc",
    limit: int = Query(default=100, le=1000),
):
    """
    Rates joined to provider groups with NPI count per group. When the NPPES GA
    subset is present, each group is also annotated with GA hospital/clinic
    counts and example org names; ?ga_hospitals_only=true keeps only groups
    touching a GA hospital.

    Powers the "compare across providers" view: one row per contracted provider
    group, ordered by price. `summary` is computed over every matching group
    (not just the returned page) so the frontend can show min/median/max without
    a second call.
    """
    conn = db()

    where, params = _price_filters(billing_code, billing_code_type, network_name, setting, npi)
    has_nppes = os.path.exists(GA_NPPES_PATH)
    order_by = "pg.negotiated_rate DESC" if sort == "rate_desc" else "pg.negotiated_rate"

    # Summary over ALL matching (group × rate) rows, before the LIMIT. Blue Value
    # has ~30 groups/code so this is cheap; the billing_code filter prunes prices
    # to a single code's partition slice first.
    summary = conn.execute(f"""
        WITH grp AS (
            SELECT pg.file_id, pg.provider_group_id, pg.negotiated_rate
            FROM {PRICE_GROUPS_SRC} pg
            WHERE {where}
            GROUP BY 1, 2, 3
        )
        SELECT MIN(negotiated_rate), MAX(negotiated_rate),
               AVG(negotiated_rate), MEDIAN(negotiated_rate),
               COUNT(*),
               COUNT(DISTINCT (file_id, provider_group_id))
        FROM grp
    """, params).fetchone()

    n_providers = conn.execute(f"""
        SELECT COUNT(DISTINCT p.npi)
        FROM {PRICE_GROUPS_SRC} pg
        JOIN {PROVIDERS_SRC} p
          ON p.file_id = pg.file_id AND p.provider_group_id = pg.provider_group_id
        WHERE {where}
    """, params).fetchone()[0]

    if has_nppes:
        ga_select = """,
            COUNT(DISTINCT ga.npi) FILTER (WHERE ga.is_hospital) AS ga_hospital_npis,
            COUNT(DISTINCT ga.npi) FILTER (WHERE ga.is_clinic)   AS ga_clinic_npis,
            COUNT(DISTINCT ga.npi)                               AS ga_npi_count,
            LIST(DISTINCT ga.org_name) FILTER (WHERE ga.org_name IS NOT NULL AND ga.org_name != '') AS ga_org_names,
            LIST(DISTINCT ga.taxonomy_group) FILTER (WHERE ga.taxonomy_group IS NOT NULL AND ga.taxonomy_group != '') AS ga_taxonomies,
            LIST(DISTINCT ga.city) FILTER (WHERE ga.city IS NOT NULL AND ga.city != '') AS ga_cities"""
        ga_join = f"LEFT JOIN read_parquet('{GA_NPPES_PATH}') ga ON p.npi = ga.npi"
        ga_having = "HAVING COUNT(DISTINCT ga.npi) FILTER (WHERE ga.is_hospital) > 0" if ga_hospitals_only else ""
    else:
        ga_select, ga_join, ga_having = "", "", ""

    rows = conn.execute(f"""
        SELECT
            pg.provider_group_id,
            pg.negotiated_rate,
            pg.negotiated_type,
            NULL AS plan_name,
            ANY_VALUE(pg.network_name) AS network_name,
            pg.expiration_date,
            COUNT(DISTINCT p.npi) AS npi_count{ga_select}
        FROM {PRICE_GROUPS_SRC} pg
        LEFT JOIN {PROVIDERS_SRC} p
            ON p.file_id = pg.file_id AND p.provider_group_id = pg.provider_group_id
        {ga_join}
        WHERE {where}
        GROUP BY pg.file_id, pg.provider_group_id, pg.negotiated_rate,
                 pg.negotiated_type, pg.expiration_date
        {ga_having}
        ORDER BY {order_by}
        LIMIT {limit}
    """, params).fetchall()

    def row(r):
        d = {
            "provider_group_id": r[0],
            "negotiated_rate":   r[1],
            "negotiated_type":   r[2],
            "plan_name":         r[3],
            "network_name":      r[4],
            "expiration_date":   r[5],
            "npi_count":         r[6],
        }
        if has_nppes:
            d["ga_hospital_npis"] = r[7]
            d["ga_clinic_npis"] = r[8]
            d["ga_npi_count"] = r[9]
            d["ga_org_names"] = (r[10] or [])[:5]
            d["ga_taxonomies"] = (r[11] or [])[:4]
            d["ga_cities"] = (r[12] or [])[:4]
        return d

    return {
        "billing_code":      billing_code,
        "billing_code_type": billing_code_type,
        "nppes_ga": has_nppes,
        "summary": {
            "min":       round(summary[0], 2) if summary[0] is not None else None,
            "max":       round(summary[1], 2) if summary[1] is not None else None,
            "avg":       round(summary[2], 2) if summary[2] is not None else None,
            "median":    round(summary[3], 2) if summary[3] is not None else None,
            "n_rows":    summary[4] or 0,
            "n_groups":  summary[5] or 0,
            "n_providers": n_providers or 0,
        },
        "results": [row(r) for r in rows],
    }


@app.get("/providers/search")
def search_providers(
    q: str = Query(default=""),
    limit: int = Query(default=20, le=100),
):
    """Search providers by name, organization, city, or NPI against the NPPES
    Georgia subset. Providers we actually hold rate data for are returned first
    (`has_rates`). A purely numeric query is treated as an NPI prefix."""
    q = q.strip()
    if not q:
        return []
    conn = db()

    # No NPPES file yet — fall back to NPI-prefix over what we've parsed.
    if not os.path.exists(GA_NPPES_PATH):
        if not q.isdigit() or not os.path.exists(NPI_LOOKUP_PATH):
            return []
        rows = conn.execute(f"""
            SELECT npi, tin_value
            FROM read_parquet('{NPI_LOOKUP_PATH}', union_by_name=true)
            WHERE CAST(npi AS VARCHAR) LIKE ?
            ORDER BY npi LIMIT {limit}
        """, [f"{q}%"]).fetchall()
        return [{"npi": r[0], "name": None, "city": None,
                 "taxonomy_group": None, "is_hospital": False,
                 "is_clinic": False, "has_rates": True} for r in rows]

    have_lookup = os.path.exists(NPI_LOOKUP_PATH)
    has_rates_expr = (
        f"g.npi IN (SELECT npi FROM read_parquet('{NPI_LOOKUP_PATH}', union_by_name=true))"
        if have_lookup else "FALSE"
    )

    if q.isdigit():
        where, params = "CAST(g.npi AS VARCHAR) LIKE ?", [f"{q}%"]
    else:
        where = ("(g.org_name ILIKE ? OR TRIM(BOTH ', ' FROM g.last_name || ', ' || g.first_name) ILIKE ? "
                 "OR g.city ILIKE ?)")
        params = [f"%{q}%", f"%{q}%", f"%{q}%"]

    rows = conn.execute(f"""
        SELECT g.npi,
               COALESCE(NULLIF(g.org_name, ''),
                        NULLIF(TRIM(BOTH ', ' FROM g.last_name || ', ' || g.first_name), ''),
                        CAST(g.npi AS VARCHAR)) AS name,
               g.city, g.taxonomy_group, g.is_hospital, g.is_clinic,
               {has_rates_expr} AS has_rates
        FROM read_parquet('{GA_NPPES_PATH}') g
        WHERE {where}
        ORDER BY has_rates DESC, g.is_hospital DESC, g.is_clinic DESC, name
        LIMIT {limit}
    """, params).fetchall()
    cols = ["npi", "name", "city", "taxonomy_group", "is_hospital", "is_clinic", "has_rates"]
    return [dict(zip(cols, r)) for r in rows]


@app.get("/plans")
def get_plans(q: str = Query(default=""), limit: int = Query(default=50, le=200)):
    """Deprecated. The pipeline never carried a real plan name (see AGENTS.md →
    Known gaps); the explorer filters by network_name via /networks instead."""
    return []


@app.get("/providers/ga")
def ga_providers(
    q: str = Query(default=""),
    hospitals_only: bool = False,
    clinics_only: bool = False,
    limit: int = Query(default=50, le=500),
):
    """Search the NPPES Georgia provider subset (org name / city / NPI prefix)."""
    if not os.path.exists(GA_NPPES_PATH):
        return {"available": False, "results": []}
    conn = db()
    conds = ["1=1"]
    params: list = []
    if q:
        conds.append("(org_name ILIKE ? OR city ILIKE ? OR CAST(npi AS VARCHAR) LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"{q}%"]
    if hospitals_only:
        conds.append("is_hospital")
    if clinics_only:
        conds.append("is_clinic")
    rows = conn.execute(f"""
        SELECT npi, entity_type, org_name, last_name, first_name,
               taxonomy_code, taxonomy_group, is_hospital, is_clinic, city, postal_code
        FROM read_parquet('{GA_NPPES_PATH}')
        WHERE {" AND ".join(conds)}
        ORDER BY is_hospital DESC, is_clinic DESC, org_name
        LIMIT {limit}
    """, params).fetchall()
    cols = ["npi", "entity_type", "org_name", "last_name", "first_name",
            "taxonomy_code", "taxonomy_group", "is_hospital", "is_clinic", "city", "postal_code"]
    return {"available": True, "results": [dict(zip(cols, r)) for r in rows]}


@app.get("/networks")
def get_networks(q: str = Query(default=""), limit: int = Query(default=100, le=500)):
    """Distinct network_name values with a price-row count as a popularity signal.
    Each price row carries exactly one network_name (== its net partition)."""
    conn = db()
    if not have_prices():
        return []
    search_filter = "AND network_name ILIKE ?" if q else ""
    params = [f"%{q}%"] if q else []
    rows = conn.execute(f"""
        SELECT network_name, COUNT(*) AS n_rates
        FROM {PRICES_SRC}
        WHERE network_name IS NOT NULL AND network_name != '' {search_filter}
        GROUP BY network_name
        ORDER BY n_rates DESC
        LIMIT {limit}
    """, params).fetchall()
    return [{"network_name": r[0], "n_rates": r[1]} for r in rows]


def _codes_have_labels(conn) -> bool:
    return os.path.exists(CODE_LABELS_PATH)


@app.get("/billing_codes")
def search_billing_codes(
    q: str = Query(default=""),
    billing_code_type: Optional[str] = None,
    limit: int = Query(default=20, le=100),
):
    """Search billing codes by consumer label / synonym / code. Empty q returns
    top codes by provider volume. Labels + RBCS categories come from
    reference/code_labels.parquet (public CMS data); the MRF's own descriptors in
    the Georgia files are near-useless ("Medical", "Surgery")."""
    conn = db()
    has_labels = _codes_have_labels(conn)
    q = q.strip()

    vol_cte = VOL_CTE

    if has_labels:
        where, params = ["1=1"], []
        if q:
            where.append("(l.billing_code ILIKE ? OR l.search_text ILIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if billing_code_type:
            where.append("l.billing_code_type = ?")
            params.append(billing_code_type)
        rows = conn.execute(f"""
            SELECT l.billing_code, l.billing_code_type, l.label,
                   l.rbcs_category, l.rbcs_subcategory, l.rbcs_family,
                   COALESCE(v.provider_groups, 0) AS provider_groups
            FROM read_parquet('{CODE_LABELS_PATH}') l
            LEFT JOIN ({vol_cte}) v
              ON l.billing_code = v.billing_code AND l.billing_code_type = v.billing_code_type
            WHERE {" AND ".join(where)}
            ORDER BY provider_groups DESC, l.rbcs_is_major DESC, l.label
            LIMIT {limit}
        """, params).fetchall()
        return [
            {"billing_code": r[0], "billing_code_type": r[1], "name": r[2], "label": r[2],
             "rbcs_category": r[3], "rbcs_subcategory": r[4], "rbcs_family": r[5],
             "provider_groups": r[6]}
            for r in rows
        ]

    # No labels file yet — code-only search over the price volume aggregate.
    conditions, params_fb = ["1=1"], []
    if q:
        conditions.append("billing_code ILIKE ?")
        params_fb.append(f"%{q}%")
    if billing_code_type:
        conditions.append("billing_code_type = ?")
        params_fb.append(billing_code_type)
    rows = conn.execute(f"""
        SELECT billing_code, billing_code_type, provider_groups
        FROM ({VOL_CTE})
        WHERE {" AND ".join(conditions)}
        ORDER BY provider_groups DESC
        LIMIT {limit}
    """, params_fb).fetchall()
    return [
        {"billing_code": r[0], "billing_code_type": r[1], "name": None, "label": None,
         "rbcs_category": None, "rbcs_subcategory": None, "rbcs_family": None,
         "provider_groups": r[2]}
        for r in rows
    ]


@app.get("/procedure_categories")
def procedure_categories():
    """RBCS categories/subcategories present in the data, with how many billing
    codes and provider groups each covers — powers the browse-by-category UI."""
    if not os.path.exists(CODE_LABELS_PATH):
        return []
    conn = db()
    rows = conn.execute(f"""
        WITH vol AS ({VOL_CTE})
        SELECT
            COALESCE(l.rbcs_category, 'Other')                 AS category,
            COALESCE(l.rbcs_subcategory, l.rbcs_category, 'Other') AS subcategory,
            COUNT(DISTINCT l.billing_code)                     AS n_codes,
            COALESCE(SUM(v.provider_groups), 0)                AS provider_groups
        FROM read_parquet('{CODE_LABELS_PATH}') l
        JOIN vol v
          ON l.billing_code = v.billing_code AND l.billing_code_type = v.billing_code_type
        GROUP BY 1, 2
        HAVING COUNT(DISTINCT l.billing_code) > 0
        ORDER BY category, provider_groups DESC
    """).fetchall()
    return [
        {"category": r[0], "subcategory": r[1], "n_codes": r[2], "provider_groups": r[3]}
        for r in rows
    ]
