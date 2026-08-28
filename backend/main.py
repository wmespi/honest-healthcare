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
    # An npi filter with no billing_code would full-scan prices (nothing prunes
    # the code axis) — it hangs. That view is the provider "menu": use
    # /providers/{npi}/procedures instead.
    if npi and not billing_code:
        raise HTTPException(
            400,
            detail="Select a procedure to see its rate distribution for this provider, "
                   "or call /providers/{npi}/procedures for the full menu.",
        )

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
    component: str = "global",
    sort: str = "rate_asc",
    limit: int = Query(default=200, le=1000),
):
    """
    "Compare across providers" — one row per contracted provider group for a
    code, ordered by price. Defaults to the global (unmodified) rate so groups
    are compared like-for-like; `component=all` keeps every modifier, or pass a
    specific one ("26", "TC").

    Each row carries the named practices behind the group (org + individual
    names) and its size; the frontend collapses the big TIN/IPA rollups and
    surfaces the nameable ones. `summary` (min/median/max/modal rate, computed
    over every matching group) supports a headline like "most contracts ~$82".
    """
    conn = db()

    where, params = _price_filters(billing_code, billing_code_type, network_name, setting, npi)
    if component == "global":
        # NULL = a file parsed before the modifier column existed; treat as global.
        where += " AND COALESCE(pg.modifier, '') = ''"
    elif component != "all":
        where += " AND pg.modifier = ?"
        params = params + [component]
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
        ),
        modal AS (
            SELECT negotiated_rate, COUNT(*) AS n
            FROM grp GROUP BY 1 ORDER BY n DESC, negotiated_rate LIMIT 1
        )
        SELECT MIN(g.negotiated_rate), MAX(g.negotiated_rate),
               AVG(g.negotiated_rate), MEDIAN(g.negotiated_rate),
               COUNT(*),
               COUNT(DISTINCT (g.file_id, g.provider_group_id)),
               (SELECT negotiated_rate FROM modal),
               (SELECT n FROM modal),
               COUNT(*) FILTER (WHERE g.negotiated_rate <= (SELECT MEDIAN(negotiated_rate) FROM grp))
        FROM grp g
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
            LIST(DISTINCT TRIM(BOTH ', ' FROM ga.last_name || ', ' || ga.first_name))
              FILTER (WHERE ga.entity_type = 'individual' AND ga.last_name IS NOT NULL AND ga.last_name != '') AS ga_indiv_names,
            LIST(DISTINCT ga.taxonomy_group) FILTER (WHERE ga.taxonomy_group IS NOT NULL AND ga.taxonomy_group != '') AS ga_taxonomies"""
        ga_join = f"LEFT JOIN read_parquet('{GA_NPPES_PATH}') ga ON p.npi = ga.npi"
        ga_having = "HAVING COUNT(DISTINCT ga.npi) FILTER (WHERE ga.is_hospital) > 0" if ga_hospitals_only else ""
    else:
        ga_select, ga_join, ga_having = "", "", ""

    # One row per contracted provider group — its rate RANGE for this code
    # (min–max across settings), not one row per (group × rate). Lets the
    # frontend rank practices without a practice appearing three times.
    grp_order = "grp_max_rate DESC, grp_min_rate DESC" if sort == "rate_desc" else "grp_min_rate, grp_max_rate"
    rows = conn.execute(f"""
        SELECT
            pg.provider_group_id,
            MIN(pg.negotiated_rate) AS grp_min_rate,
            MAX(pg.negotiated_rate) AS grp_max_rate,
            MEDIAN(pg.negotiated_rate) AS grp_median_rate,
            ANY_VALUE(pg.negotiated_type) AS negotiated_type,
            ANY_VALUE(pg.network_name) AS network_name,
            COUNT(DISTINCT p.npi) AS npi_count{ga_select}
        FROM {PRICE_GROUPS_SRC} pg
        LEFT JOIN {PROVIDERS_SRC} p
            ON p.file_id = pg.file_id AND p.provider_group_id = pg.provider_group_id
        {ga_join}
        WHERE {where}
        GROUP BY pg.file_id, pg.provider_group_id
        {ga_having}
        ORDER BY {grp_order}
        LIMIT {limit}
    """, params).fetchall()

    ROLLUP_THRESHOLD = 40  # groups bigger than this can't be meaningfully named

    def row(r):
        d = {
            "provider_group_id": r[0],
            "min_rate":          round(r[1], 2) if r[1] is not None else None,
            "max_rate":          round(r[2], 2) if r[2] is not None else None,
            "median_rate":       round(r[3], 2) if r[3] is not None else None,
            "negotiated_rate":   round(r[1], 2) if r[1] is not None else None,  # back-compat
            "negotiated_type":   r[4],
            "network_name":      r[5],
            "npi_count":         r[6],
        }
        if has_nppes:
            # ga_select order: hospital_npis, clinic_npis, npi_count, org_names,
            #                  indiv_names, taxonomies  →  r[7..12]
            npi_count = r[6] or 0
            orgs = r[10] or []
            indiv = r[11] or []
            d["ga_hospital_npis"] = r[7]
            d["ga_clinic_npis"] = r[8]
            d["ga_npi_count"] = r[9]
            d["ga_org_names"] = orgs[:5]
            d["ga_indiv_names"] = indiv[:5]
            d["ga_taxonomies"] = (r[12] or [])[:4]
            d["is_rollup"] = npi_count > ROLLUP_THRESHOLD
            d["named_practices"] = ([] if d["is_rollup"]
                                    else (orgs[:3] or [n.title() for n in indiv[:3]]))
        return d

    median = round(summary[3], 2) if summary[3] is not None else None
    return {
        "billing_code":      billing_code,
        "billing_code_type": billing_code_type,
        "component":         component,
        "nppes_ga": has_nppes,
        "summary": {
            "min":       round(summary[0], 2) if summary[0] is not None else None,
            "max":       round(summary[1], 2) if summary[1] is not None else None,
            "avg":       round(summary[2], 2) if summary[2] is not None else None,
            "median":    median,
            "n_rows":    summary[4] or 0,
            "n_groups":  summary[5] or 0,
            "n_providers": n_providers or 0,
            "modal_rate": round(summary[6], 2) if summary[6] is not None else None,
            "n_at_modal": summary[7] or 0,
            "n_at_or_below_median": summary[8] or 0,
        },
        "results": [row(r) for r in rows],
    }


_POS_LABELS = {
    "office": "Office / telehealth",
    "asc": "Ambulatory surgery center",
    "er": "Emergency room",
    "inpatient": "Hospital inpatient",
    "hosp_outpatient": "Hospital outpatient dept.",
    "any": "Any setting",
    "unspecified": "Setting not specified",
    "facility": "Facility setting",
}


def _pos_bucket(service_code: Optional[str]) -> str:
    """Collapse a `|`-joined CMS place-of-service list to one consumer bucket.
    A long list means one rate that applies across settings -> "any"."""
    codes = {c for c in (service_code or "").split("|") if c}
    if not codes:
        return "unspecified"
    if len(codes) >= 4:
        return "any"
    if "24" in codes:
        return "asc"
    if "23" in codes:
        return "er"
    if codes & {"21", "51", "61"}:
        return "inpatient"
    if codes & {"22", "19", "20"}:
        return "hosp_outpatient"
    if codes & {"11", "10", "12", "02", "72"}:
        return "office"
    return "facility"


_MODIFIER_LABELS = {
    "": ("Full procedure", "The complete service — physician work plus facility/equipment."),
    "26": ("Professional fee", "The physician's work only — reading, interpretation, supervision."),
    "TC": ("Technical fee", "Facility, equipment, and staff only — no physician work."),
    "26|TC": ("Professional + technical", "Billed as separate components that sum to the global rate."),
    "QW": ("CLIA-waived test", "Simple lab test run in-office."),
    "53": ("Discontinued procedure", "Stopped before completion."),
    "50": ("Bilateral procedure", "Performed on both sides."),
}


@app.get("/rates/quote")
def rate_quote(
    billing_code: str,
    npi: int,
    billing_code_type: str = "CPT",
    network_name: Optional[str] = None,
):
    """Job 1 — "what will this procedure cost at this provider". Resolves the NPI
    to its group-sets first (cheap), then the code's prices, and organises them
    by component modifier (global / professional / technical) and place of
    service. Returns a headline rate + the breakdown."""
    conn = db()

    net_filter = "AND p.net = ?" if network_name else ""
    params: list = [npi, billing_code, billing_code_type]
    if network_name:
        params.append(network_slug(network_name))

    rows = conn.execute(f"""
        WITH npi_groups AS (
            SELECT DISTINCT file_id, provider_group_id
            FROM {PROVIDERS_SRC} WHERE npi = ?
        ),
        npi_sets AS (
            SELECT DISTINCT gs.file_id, gs.group_set_id
            FROM {GROUP_SETS_SRC} gs
            JOIN npi_groups g
              ON g.file_id = gs.file_id AND g.provider_group_id = gs.provider_group_id
        )
        SELECT p.modifier, p.service_code, p.setting, p.negotiated_type,
               MIN(p.negotiated_rate), MAX(p.negotiated_rate), COUNT(*)
        FROM {PRICES_SRC} p
        JOIN npi_sets s ON s.file_id = p.file_id AND s.group_set_id = p.group_set_id
        WHERE p.billing_code = ? AND p.billing_code_type = ? {net_filter}
        GROUP BY 1, 2, 3, 4
    """, params).fetchall()

    if not rows:
        raise HTTPException(404, detail=f"No rate for {billing_code_type}:{billing_code} at this provider")

    # Fold (modifier, pos_bucket) → rate range.
    comps: dict = {}
    for modifier, service_code, setting, ntype, lo, hi, n in rows:
        mod = modifier or ""
        bucket = _pos_bucket(service_code)
        c = comps.setdefault(mod, {})
        s = c.setdefault(bucket, {"min": lo, "max": hi, "n": 0, "negotiated_type": ntype})
        s["min"] = min(s["min"], lo)
        s["max"] = max(s["max"], hi)
        s["n"] += n

    def comp_block(mod):
        label, desc = _MODIFIER_LABELS.get(mod, (f"Modifier {mod}", ""))
        settings = sorted(
            (
                {
                    "pos_bucket": b,
                    "pos_label": _POS_LABELS.get(b, b),
                    "min_rate": round(v["min"], 2),
                    "max_rate": round(v["max"], 2),
                    "negotiated_type": v["negotiated_type"],
                }
                for b, v in comps[mod].items()
            ),
            key=lambda x: x["min_rate"],
        )
        return {"modifier": mod, "label": label, "description": desc, "settings": settings}

    order = sorted(comps.keys(), key=lambda m: (m != "", m))  # global first
    components = [comp_block(m) for m in order]

    # Headline: the range of the global (no-modifier) rate across settings. If
    # there's no global rate (component-split only), fall back to the full spread
    # and flag it so the UI can say "billed as parts".
    glob = next((c for c in components if c["modifier"] == ""), None)
    if glob and glob["settings"]:
        lo = min(s["min_rate"] for s in glob["settings"])
        hi = max(s["max_rate"] for s in glob["settings"])
        one_setting = len(glob["settings"]) == 1
        headline = {"rate": lo, "max_rate": hi, "basis": "global",
                    "pos_label": glob["settings"][0]["pos_label"] if one_setting else None}
    else:
        allmin = [s["min_rate"] for c in components for s in c["settings"]]
        allmax = [s["max_rate"] for c in components for s in c["settings"]]
        headline = {"rate": min(allmin), "max_rate": max(allmax),
                    "basis": "component", "pos_label": None}

    split = "26" in comps and "TC" in comps
    return {
        "billing_code": billing_code,
        "billing_code_type": billing_code_type,
        "npi": npi,
        "network_name": network_name,
        "headline": headline,
        "components": components,
        "is_component_split": split,
    }


@app.get("/providers/{npi}/procedures")
def provider_procedures(
    npi: int,
    network_name: Optional[str] = None,
    setting: Optional[str] = None,
    q: str = Query(default=""),
    limit: int = Query(default=500, le=2000),
):
    """The provider "menu": every procedure this NPI has a negotiated rate for,
    with the rate range, grouped-ready by RBCS category.

    Resolves the NPI to its (file_id, group_set_id) sets FIRST (cheap — the
    providers filter is selective, then a join to the small group_sets table),
    then touches `prices`. This is what makes it affordable where a bare
    npi filter on /rates/distribution is not.
    """
    conn = db()

    net_filter = "AND p.net = ?" if network_name else ""
    set_filter = "AND p.setting = ?" if setting else ""
    params: list = [npi]
    if network_name:
        params.append(network_slug(network_name))
    if setting:
        params.append(setting)

    has_labels = os.path.exists(CODE_LABELS_PATH)
    if has_labels:
        label_join = f"LEFT JOIN read_parquet('{CODE_LABELS_PATH}') l ON l.billing_code = m.billing_code AND l.billing_code_type = m.billing_code_type"
        label_cols = "l.label, l.rbcs_category, l.rbcs_subcategory"
        search_filter = "WHERE (m.billing_code ILIKE ? OR l.search_text ILIKE ?)" if q else ""
    else:
        label_join, label_cols = "", "NULL AS label, NULL AS rbcs_category, NULL AS rbcs_subcategory"
        search_filter = "WHERE m.billing_code ILIKE ?" if q else ""

    rows = conn.execute(f"""
        WITH npi_groups AS (
            SELECT DISTINCT file_id, provider_group_id
            FROM {PROVIDERS_SRC}
            WHERE npi = ?
        ),
        npi_sets AS (
            SELECT DISTINCT gs.file_id, gs.group_set_id
            FROM {GROUP_SETS_SRC} gs
            JOIN npi_groups g
              ON g.file_id = gs.file_id AND g.provider_group_id = gs.provider_group_id
        ),
        menu AS (
            SELECT
                p.billing_code, p.billing_code_type,
                -- range over the *global* (unmodified) rate when the code has one,
                -- so 26/TC component fees don't widen it misleadingly
                MIN(p.negotiated_rate)    FILTER (WHERE p.modifier = '') AS g_min,
                MEDIAN(p.negotiated_rate) FILTER (WHERE p.modifier = '') AS g_med,
                MAX(p.negotiated_rate)    FILTER (WHERE p.modifier = '') AS g_max,
                COUNT(*)                  FILTER (WHERE p.modifier = '') AS g_n,
                MIN(p.negotiated_rate)    AS a_min,
                MEDIAN(p.negotiated_rate) AS a_med,
                MAX(p.negotiated_rate)    AS a_max,
                COUNT(*)                  AS n_rates,
                COUNT(DISTINCT p.network_name) AS n_networks,
                (COUNT(*) FILTER (WHERE p.modifier = '26') > 0
                 AND COUNT(*) FILTER (WHERE p.modifier = 'TC') > 0) AS is_split
            FROM {PRICES_SRC} p
            JOIN npi_sets s ON s.file_id = p.file_id AND s.group_set_id = p.group_set_id
            WHERE 1=1 {net_filter} {set_filter}
            GROUP BY 1, 2
        )
        SELECT m.billing_code, m.billing_code_type,
               ROUND(COALESCE(m.g_min, m.a_min), 2),
               ROUND(COALESCE(m.g_med, m.a_med), 2),
               ROUND(COALESCE(m.g_max, m.a_max), 2),
               m.n_rates, m.n_networks, m.is_split, (m.g_n > 0) AS has_global,
               {label_cols}
        FROM menu m
        {label_join}
        {search_filter}
        ORDER BY m.n_rates DESC, m.billing_code
        LIMIT {limit}
    """, params + ([f"%{q}%", f"%{q}%"] if q and has_labels else ([f"%{q}%"] if q else []))).fetchall()

    return {
        "npi": npi,
        "count": len(rows),
        "results": [
            {
                "billing_code": r[0], "billing_code_type": r[1],
                "min_rate": r[2], "median_rate": r[3], "max_rate": r[4],
                "n_rates": r[5], "n_networks": r[6],
                "is_split": bool(r[7]), "has_global": bool(r[8]),
                "label": r[9], "rbcs_category": r[10], "rbcs_subcategory": r[11],
            }
            for r in rows
        ],
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
