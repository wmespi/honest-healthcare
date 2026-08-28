"""Reference / browse endpoints.

  /networks              distinct network_name values (+ price-row count)
  /billing_codes         consumer-label / synonym / code search
  /procedure_categories  RBCS categories present in the data
  /plans                 deprecated — always []
"""
import os
from typing import Optional

from fastapi import APIRouter, Query

from ..data_sources import CODE_LABELS_PATH, PRICES_SRC, VOL_CTE, db, have_prices

router = APIRouter()


@router.get("/networks")
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


@router.get("/billing_codes")
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
    has_labels = os.path.exists(CODE_LABELS_PATH)
    q = q.strip()

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
            LEFT JOIN ({VOL_CTE}) v
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


@router.get("/procedure_categories")
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


@router.get("/plans")
def get_plans(q: str = Query(default=""), limit: int = Query(default=50, le=200)):
    """Deprecated. The pipeline never carried a real plan name (see
    docs/known-gaps.md); the explorer filters by network_name via /networks."""
    return []
