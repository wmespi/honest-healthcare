"""Provider endpoints.

  /providers/{npi}/procedures  job 4 — the provider "menu"
  /providers/search            name / org / city / NPI search (NPPES GA subset)
  /providers/ga                raw NPPES GA lookup
"""
import os
from typing import Optional

from fastapi import APIRouter, Query

from ..data_sources import (
    GA_NPPES_PATH,
    CODE_LABELS_PATH,
    NPI_LOOKUP_PATH,
    PRICES_SRC,
    PROVIDERS_SRC,
    GROUP_SETS_SRC,
    db,
    network_slug,
)
from ..labels import nucc_bits, provider_card
from ..evidence import billed_codes

router = APIRouter()


@router.get("/providers/{npi}/procedures")
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

    pcard = provider_card(conn, npi)
    if pcard:
        pcard.pop("_grouping", None)
        pcard.pop("_classification", None)

    # Badge rows this NPI actually billed to Medicare Part B (issue #14).
    # Empty dict until `make cms-utilization` has run.
    billed = billed_codes(conn, npi, [r[0] for r in rows])

    return {
        "npi": npi,
        "provider": pcard,
        "count": len(rows),
        "results": [
            {
                "billing_code": r[0], "billing_code_type": r[1],
                "min_rate": r[2], "median_rate": r[3], "max_rate": r[4],
                "n_rates": r[5], "n_networks": r[6],
                "is_split": bool(r[7]), "has_global": bool(r[8]),
                "label": r[9], "rbcs_category": r[10], "rbcs_subcategory": r[11],
                "medicare": billed.get(r[0]),
            }
            for r in rows
        ],
    }


@router.get("/providers/search")
def search_providers(
    q: str = Query(default=""),
    specialty: str = Query(default=""),
    limit: int = Query(default=20, le=100),
):
    """Search providers by name, organization, city, or NPI against the NPPES
    Georgia subset. Providers we actually hold rate data for are returned first
    (`has_rates`). A purely numeric query is treated as an NPI prefix. `specialty`
    filters on the NUCC label (e.g. "cardio", "orthopa")."""
    q = q.strip()
    specialty = specialty.strip()
    if not q and not specialty:
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

    conds, params = [], []
    if q.isdigit():
        conds.append("CAST(g.npi AS VARCHAR) LIKE ?")
        params.append(f"{q}%")
    elif q:
        conds.append("(g.org_name ILIKE ? OR TRIM(BOTH ', ' FROM g.last_name || ', ' || g.first_name) ILIKE ? "
                     "OR g.city ILIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    spec_sel, spec_join = nucc_bits()
    if specialty:
        conds.append("(COALESCE(nx.specialty, '') ILIKE ? OR COALESCE(nx.classification, '') ILIKE ? "
                     "OR COALESCE(nx.grouping, '') ILIKE ?)" if spec_join
                     else "g.taxonomy_group ILIKE ?")
        params += ([f"%{specialty}%"] * 3 if spec_join else [f"%{specialty}%"])
    where = " AND ".join(conds) if conds else "1=1"

    rows = conn.execute(f"""
        SELECT g.npi,
               COALESCE(NULLIF(g.org_name, ''),
                        NULLIF(TRIM(BOTH ', ' FROM g.last_name || ', ' || g.first_name), ''),
                        CAST(g.npi AS VARCHAR)) AS name,
               g.city, g.taxonomy_group, g.is_hospital, g.is_clinic,
               {has_rates_expr} AS has_rates,
               {spec_sel}
        FROM read_parquet('{GA_NPPES_PATH}') g
        {spec_join}
        WHERE {where}
        ORDER BY has_rates DESC, g.is_hospital DESC, g.is_clinic DESC, name
        LIMIT {limit}
    """, params).fetchall()
    cols = ["npi", "name", "city", "taxonomy_group", "is_hospital", "is_clinic", "has_rates", "specialty"]
    return [dict(zip(cols, r)) for r in rows]


@router.get("/providers/ga")
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
