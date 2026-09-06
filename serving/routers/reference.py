"""Reference / browse endpoints.

  /networks              distinct network_name values (+ price-row count)
  /billing_codes         consumer-label / synonym / code search
  /procedure_categories  RBCS categories present in the data
  /plans                 friendly plan name -> network_name (curated, GH #33)

The first three read the precomputed `rate_hist` / `code_dim` — never a `rates`
scan, which is ~645M rows store-wide (issue #10).
"""
import json
import os
from typing import Optional

from fastapi import APIRouter, Query

from ..data_sources import CODE_DIM_SRC, RATE_HIST_SRC, db

router = APIRouter()


@router.get("/networks")
def get_networks(q: str = Query(default=""), limit: int = Query(default=100, le=500)):
    """Distinct network_name values with a price-row count as a popularity signal
    — summed off `rate_hist` (exact; `n_rates` is the raw row count, unweighted
    by roster size)."""
    conn = db()
    search_filter = "AND network_name ILIKE ?" if q else ""
    params = [f"%{q}%"] if q else []
    rows = conn.execute(f"""
        SELECT network_name, SUM(n_rates) AS n_rates
        FROM {RATE_HIST_SRC}
        WHERE network_name IS NOT NULL AND network_name != '' {search_filter}
        GROUP BY network_name
        ORDER BY n_rates DESC
        LIMIT {limit}
    """, params).fetchall()
    return [{"network_name": r[0], "n_rates": int(r[1])} for r in rows]


@router.get("/billing_codes")
def search_billing_codes(
    q: str = Query(default=""),
    billing_code_type: Optional[str] = None,
    limit: int = Query(default=20, le=100),
):
    """Search billing codes by consumer label / synonym / code. Empty q returns
    top codes by provider volume (roster-weighted, off `rate_hist`)."""
    conn = db()
    q = q.strip()
    where, params = ["1=1"], []
    if q:
        where.append("(cd.billing_code ILIKE ? OR cd.search_text ILIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if billing_code_type:
        where.append("cd.billing_code_type = ?")
        params.append(billing_code_type)
    rows = conn.execute(f"""
        WITH vol AS (
            SELECT billing_code, billing_code_type, SUM(n) AS provider_groups
            FROM {RATE_HIST_SRC}
            GROUP BY 1, 2
        )
        SELECT cd.billing_code, cd.billing_code_type, cd.label,
               cd.category, cd.rbcs_subcategory, cd.rbcs_family,
               COALESCE(v.provider_groups, 0) AS provider_groups
        FROM {CODE_DIM_SRC} cd
        LEFT JOIN vol v
          ON cd.billing_code = v.billing_code AND cd.billing_code_type = v.billing_code_type
        WHERE {" AND ".join(where)}
        ORDER BY provider_groups DESC, cd.rbcs_is_major DESC, cd.label
        LIMIT {limit}
    """, params).fetchall()
    return [
        {"billing_code": r[0], "billing_code_type": r[1], "name": r[2], "label": r[2],
         "rbcs_category": r[3], "rbcs_subcategory": r[4], "rbcs_family": r[5],
         "provider_groups": int(r[6])}
        for r in rows
    ]


@router.get("/procedure_categories")
def procedure_categories():
    """RBCS categories/subcategories present in the data, with how many billing
    codes and provider groups each covers — powers the browse-by-category UI."""
    conn = db()
    rows = conn.execute(f"""
        WITH vol AS (
            SELECT billing_code, billing_code_type, SUM(n) AS provider_groups
            FROM {RATE_HIST_SRC}
            GROUP BY 1, 2
        )
        SELECT
            COALESCE(cd.category, 'Other')                       AS category,
            COALESCE(cd.rbcs_subcategory, cd.category, 'Other')   AS subcategory,
            COUNT(DISTINCT cd.billing_code)                       AS n_codes,
            COALESCE(SUM(v.provider_groups), 0)                   AS provider_groups
        FROM {CODE_DIM_SRC} cd
        JOIN vol v
          ON cd.billing_code = v.billing_code AND cd.billing_code_type = v.billing_code_type
        GROUP BY 1, 2
        HAVING COUNT(DISTINCT cd.billing_code) > 0
        ORDER BY category, provider_groups DESC
    """).fetchall()
    return [
        {"category": r[0], "subcategory": r[1], "n_codes": r[2], "provider_groups": int(r[3])}
        for r in rows
    ]


_PLAN_MAP_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plan_networks.json")


@router.get("/plans")
def get_plans(q: str = Query(default="")):
    """Friendly plan names → the network_name the rate store filters by (GH #33).

    The pipeline never carried a real plan name (docs/known-gaps.md), so this is
    a hand-curated bridge in serving/plan_networks.json. Each entry is marked
    `available` if at least one of its networks actually has rates loaded.
    """
    try:
        with open(_PLAN_MAP_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []

    conn = db()
    have = {r[0] for r in conn.execute(
        f"SELECT DISTINCT network_name FROM {RATE_HIST_SRC} WHERE network_name IS NOT NULL"
    ).fetchall()}

    ql = q.strip().lower()
    out = []
    for p in data.get("plans", []):
        nets = p.get("network_names", [])
        primary = next((n for n in nets if n in have), None)
        if ql and ql not in p["plan"].lower() and not any(ql in a for a in p.get("aliases", [])) \
                and ql not in p.get("carrier", "").lower():
            continue
        out.append({
            "plan": p["plan"],
            "carrier": p.get("carrier"),
            "market": p.get("market"),
            "network_name": primary or (nets[0] if nets else None),
            "available": primary is not None,
        })
    # available plans first
    out.sort(key=lambda x: (not x["available"], x["plan"]))
    return out
