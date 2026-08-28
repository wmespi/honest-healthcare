import os
import duckdb
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

DATA_DIR        = os.getenv("DATA_DIR", "/app/data")
RATES_GLOB      = f"{DATA_DIR}/anthem/rates/*.parquet"
PROVIDERS_GLOB  = f"{DATA_DIR}/anthem/providers/*.parquet"
CODES_GLOB      = f"{DATA_DIR}/anthem/codes/*.parquet"
NPI_LOOKUP_PATH = f"{DATA_DIR}/anthem/npi_lookup.parquet"
GA_NPPES_PATH   = f"{DATA_DIR}/nppes/ga_providers.parquet"
CODE_LABELS_PATH = f"{DATA_DIR}/reference/code_labels.parquet"

app = FastAPI(title="Honest Healthcare API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def db():
    return duckdb.connect()


def rates_has_column(conn, name: str) -> bool:
    """True if `name` is present in the unioned rates schema. network_name only
    appears once at least one new-format parquet (post structured-attribution)
    has been written, so filters/endpoints that use it must degrade gracefully."""
    try:
        cols = conn.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{RATES_GLOB}', union_by_name=true)"
        ).fetchall()
        return any(c[0] == name for c in cols)
    except Exception:
        return False


@app.get("/")
def health():
    conn = db()
    try:
        rates     = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{RATES_GLOB}', union_by_name=true)").fetchone()[0]
        providers = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{PROVIDERS_GLOB}', union_by_name=true)").fetchone()[0]
        return {"status": "ok", "total_rates": rates, "total_providers": providers}
    except Exception as e:
        return {"status": "ok", "note": str(e)}


@app.get("/rates/distribution")
def rate_distribution(
    billing_code: Optional[str] = None,
    billing_code_type: str = "CPT",
    plan_name: Optional[str] = None,
    network_name: Optional[str] = None,
    setting: Optional[str] = None,
    npi: Optional[int] = None,
):
    """
    Rate distribution. When billing_code is omitted, returns a network-wide overview
    (pre-bucketed at the SQL level to keep the response size manageable).
    """
    conn = db()

    conditions = ["1=1"]
    params: list = []

    if billing_code:
        conditions += ["billing_code = ?", "billing_code_type = ?"]
        params += [billing_code, billing_code_type]
    if plan_name:
        conditions.append("plan_name = ?")
        params.append(plan_name)
    if network_name and rates_has_column(conn, "network_name"):
        conditions.append("list_contains(list_transform(string_split(network_name, '|'), x -> trim(x)), ?)")
        params.append(network_name)
    if setting:
        conditions.append("setting = ?")
        params.append(setting)
    if npi:
        conditions.append(f"provider_group_id IN (SELECT provider_group_id FROM read_parquet('{PROVIDERS_GLOB}', union_by_name=true) WHERE npi = ?)")
        params.append(npi)

    where = " AND ".join(conditions)

    if not billing_code:
        # Network overview: pre-bucket into $50 intervals up to $2000, then one overflow bucket.
        # Avoids returning hundreds of thousands of distinct rate values.
        dist = conn.execute(f"""
            SELECT
                FLOOR(LEAST(negotiated_rate, 2000) / 50) * 50 AS rate,
                'fee schedule' AS negotiated_type,
                COUNT(DISTINCT provider_group_id) AS provider_groups
            FROM read_parquet('{RATES_GLOB}', union_by_name=true)
            WHERE {where}
            GROUP BY 1, 2
            ORDER BY 1
        """, params).fetchall()
    else:
        dist = conn.execute(f"""
            SELECT
                negotiated_rate,
                negotiated_type,
                COUNT(DISTINCT provider_group_id) AS provider_groups
            FROM read_parquet('{RATES_GLOB}', union_by_name=true)
            WHERE {where}
            GROUP BY negotiated_rate, negotiated_type
            ORDER BY negotiated_rate
        """, params).fetchall()

    if not dist:
        label = f"{billing_code_type}:{billing_code}" if billing_code else "network"
        raise HTTPException(404, detail=f"No rates found for {label}")

    stats = conn.execute(f"""
        SELECT
            MIN(negotiated_rate),
            MAX(negotiated_rate),
            AVG(negotiated_rate),
            MEDIAN(negotiated_rate),
            COUNT(DISTINCT provider_group_id),
            COUNT(*)
        FROM read_parquet('{RATES_GLOB}', union_by_name=true)
        WHERE {where}
    """, params).fetchone()

    return {
        "billing_code":      billing_code or "ALL",
        "billing_code_type": billing_code_type if billing_code else "NETWORK",
        "summary": {
            "min":             round(stats[0], 2),
            "max":             round(stats[1], 2),
            "avg":             round(stats[2], 2),
            "median":          round(stats[3], 2),
            "provider_groups": stats[4],
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
    limit: int = Query(default=100, le=1000),
):
    """
    Rates joined to provider groups with NPI count per group. When the NPPES GA
    subset is present, each group is also annotated with GA hospital/clinic
    counts and example org names; ?ga_hospitals_only=true keeps only groups
    touching a GA hospital.
    """
    conn = db()

    conditions = ["r.billing_code = ?", "r.billing_code_type = ?"]
    params: list = [billing_code, billing_code_type]

    if plan_name:
        conditions.append("r.plan_name = ?")
        params.append(plan_name)
    has_network = rates_has_column(conn, "network_name")
    if network_name and has_network:
        conditions.append("list_contains(list_transform(string_split(r.network_name, '|'), x -> trim(x)), ?)")
        params.append(network_name)
    if setting:
        conditions.append("r.setting = ?")
        params.append(setting)
    if npi:
        conditions.append(f"r.provider_group_id IN (SELECT provider_group_id FROM read_parquet('{PROVIDERS_GLOB}', union_by_name=true) WHERE npi = ?)")
        params.append(npi)

    where = " AND ".join(conditions)
    has_nppes = os.path.exists(GA_NPPES_PATH)

    if has_nppes:
        ga_select = """,
            COUNT(DISTINCT ga.npi) FILTER (WHERE ga.is_hospital) AS ga_hospital_npis,
            COUNT(DISTINCT ga.npi) FILTER (WHERE ga.is_clinic)   AS ga_clinic_npis,
            LIST(DISTINCT ga.org_name) FILTER (WHERE ga.org_name IS NOT NULL AND ga.org_name != '') AS ga_org_names"""
        ga_join = f"LEFT JOIN read_parquet('{GA_NPPES_PATH}') ga ON p.npi = ga.npi"
        ga_having = "HAVING COUNT(DISTINCT ga.npi) FILTER (WHERE ga.is_hospital) > 0" if ga_hospitals_only else ""
    else:
        ga_select, ga_join, ga_having = "", "", ""

    rows = conn.execute(f"""
        SELECT
            r.provider_group_id,
            r.negotiated_rate,
            r.negotiated_type,
            r.plan_name,
            {"ANY_VALUE(r.network_name)" if has_network else "NULL"} AS network_name,
            r.expiration_date,
            COUNT(DISTINCT p.npi) AS npi_count{ga_select}
        FROM read_parquet('{RATES_GLOB}', union_by_name=true) r
        LEFT JOIN read_parquet('{PROVIDERS_GLOB}', union_by_name=true) p
            ON r.provider_group_id = p.provider_group_id
        {ga_join}
        WHERE {where}
        GROUP BY r.provider_group_id, r.negotiated_rate, r.negotiated_type,
                 r.plan_name, r.expiration_date
        {ga_having}
        ORDER BY r.negotiated_rate
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
            d["ga_org_names"] = (r[9] or [])[:5]
        return d

    return {
        "billing_code":      billing_code,
        "billing_code_type": billing_code_type,
        "nppes_ga": has_nppes,
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
    """Distinct plan names. Splits pipe-joined plan_name values into individual names."""
    conn = db()
    search_filter = "AND plan ILIKE ?" if q else ""
    params = [f"%{q}%"] if q else []
    rows = conn.execute(f"""
        SELECT DISTINCT TRIM(plan) AS plan_name
        FROM (
            SELECT UNNEST(string_split(plan_name, ' | ')) AS plan
            FROM read_parquet('{RATES_GLOB}', union_by_name=true)
            WHERE plan_name IS NOT NULL AND plan_name != ''
        )
        WHERE plan IS NOT NULL AND TRIM(plan) != ''
        {search_filter}
        ORDER BY plan_name
        LIMIT {limit}
    """, params).fetchall()
    return [r[0] for r in rows]


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
    """Distinct network_name values (structured attribution from provider_references)."""
    conn = db()
    if not rates_has_column(conn, "network_name"):
        return []  # no new-format parquet yet — nothing to list
    # A rate row's network_name is '|'-joined when the provider_reference carried
    # several networks — split them so each network is one clean dropdown entry.
    search_filter = "AND net ILIKE ?" if q else ""
    params = [f"%{q}%"] if q else []
    rows = conn.execute(f"""
        SELECT net AS network_name, COUNT(*) AS n_rates
        FROM (
            SELECT TRIM(UNNEST(string_split(network_name, '|'))) AS net
            FROM read_parquet('{RATES_GLOB}', union_by_name=true)
            WHERE network_name IS NOT NULL AND network_name != ''
        )
        WHERE net IS NOT NULL AND net != ''
        {search_filter}
        GROUP BY net
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

    vol_cte = f"""
        SELECT billing_code, billing_code_type,
               COUNT(DISTINCT provider_group_id) AS provider_groups
        FROM read_parquet('{RATES_GLOB}', union_by_name=true)
        GROUP BY billing_code, billing_code_type
    """

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

    # No labels file yet — rates-only search by code.
    conditions, params_fb = ["1=1"], []
    if q:
        conditions.append("billing_code ILIKE ?")
        params_fb.append(f"%{q}%")
    if billing_code_type:
        conditions.append("billing_code_type = ?")
        params_fb.append(billing_code_type)
    rows = conn.execute(f"""
        SELECT billing_code, billing_code_type,
               COUNT(DISTINCT provider_group_id) AS provider_groups
        FROM read_parquet('{RATES_GLOB}', union_by_name=true)
        WHERE {" AND ".join(conditions)}
        GROUP BY billing_code, billing_code_type
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
        WITH vol AS (
            SELECT billing_code, billing_code_type,
                   COUNT(DISTINCT provider_group_id) AS provider_groups
            FROM read_parquet('{RATES_GLOB}', union_by_name=true)
            GROUP BY 1, 2
        )
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
