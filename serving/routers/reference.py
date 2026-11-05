"""Reference / browse endpoints.

  /networks                     distinct network_name values (+ price-row count)
  /imaging/plans                friendly plan name -> network_name (curated, GH #33)
  /imaging/plans/cost_sharing   sourced deductible / out-of-pocket max / imaging cost share per plan version
  /service_lines                curated taxonomy and billing-code allowlists, and the imaging menu

The two /imaging/plans routes are the imaging page's, and go with it in step 11
of #135; `/plans` is the find-care screen's (routers/find_care.py).

`/networks` reads the precomputed `rate_hist`, never a `rates` scan.
"""
import json
import os

from fastapi import APIRouter, Query

from ..data_sources import RATE_HIST_SRC, db
from ..service_lines import SERVICE_LINES, SERVICE_LINE_BILLING_CODES, SERVICE_LINE_MENUS

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


_PLAN_MAP_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plan_networks.json")


@router.get("/imaging/plans")
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


_COST_SHARING_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plan_cost_sharing.json")


@router.get("/imaging/plans/cost_sharing")
def get_plan_cost_sharing(network_name: str = Query(...)):
    """The plan versions on one network with the cost sharing copied from each
    Summary of Benefits and Coverage, so the estimator starts from the plan's
    own numbers instead of the member's memory. Empty when none are sourced."""
    try:
        with open(_COST_SHARING_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [v for v in data.get("variants", []) if v.get("network_name") == network_name]


@router.get("/service_lines")
def get_service_lines():
    """The curated service-line taxonomy and billing-code allowlists
    (`serving/service_lines.py`), so the frontend reads them instead of keeping
    its own copy."""
    return {
        name: {"taxonomy_codes": codes,
               "billing_codes": SERVICE_LINE_BILLING_CODES.get(name, []),
               "menu": [{"code": c, "label": l, "group": g}
                        for c, l, g in SERVICE_LINE_MENUS.get(name, [])]}
        for name, codes in SERVICE_LINES.items()
    }
