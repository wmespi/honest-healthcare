"""Imaging endpoint — "where can I get this scan, and what does it cost".

  /imaging/providers   one row per billing practice that reads or performs
                       imaging, priced for one scan on one plan

A price here is the *professional* side of a scan as Anthem files it, not a
facility charge (`facility_estimate` is Medicare's rate for the hospital's part,
labelled an estimate): a freestanding imaging center bills one global rate; a
hospital-based radiologist bills only the reading (modifier 26) and the
hospital bills the technical part (TC) separately, in a file we do not carry.
The row says which of those it is (`basis`) so the UI never presents a partial
price as a full one.
"""
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..data_sources import (
    EVIDENCE_SRC,
    GROUP_MEMBERS_SRC,
    PROVIDER_DIM_SRC,
    RATE_GROUPS_SRC,
    ZIP_CENTROIDS_SRC,
    db,
    rate_filters,
)
from ..benchmark import opps_payment
from ..ranking import haversine_sql
from ..service_lines import SERVICE_LINE_BILLING_CODES

router = APIRouter()

COMPONENTS = {"": "global", "TC": "technical", "26": "professional"}


def _stats(lo, med, hi):
    if med is None:
        return None
    return {"min": round(lo, 2), "typical": round(med, 2), "max": round(hi, 2)}


def _same_practice(name):
    """One key for "THE EMORY CLINIC, INC." and "THE EMORY CLINIC INC": a
    practice files under many TINs, one row per TIN would repeat it."""
    key = re.sub(r"[^A-Z0-9 ]", "", name.upper())
    return re.sub(r"( (INC|LLC|PC|PA|LLP|LP|CORP|PLLC))+$", "", key).strip()


def _price(glob, tech, pro):
    """(basis, typical): the whole price when we can form one, else the part we hold."""
    if glob:
        return "global", glob["typical"]
    if tech and pro:
        return "split", round(tech["typical"] + pro["typical"], 2)
    if pro:
        return "professional_only", pro["typical"]
    if tech:
        return "technical_only", tech["typical"]
    return None, None


# A price line applies in an office when its place-of-service list names 11 or
# says it holds everywhere (CSTM-00). A line for hospital or facility sites only
# is a different price for a different visit and never joins a practice's quote.
OFFICE_POS = ("(COALESCE(pg.service_code, '') IN ('', 'CSTM-00') "
              "OR CONTAINS('|' || pg.service_code || '|', '|11|'))")


def _facility_estimate(conn, billing_code):
    """Medicare's national hospital-outpatient rate for the scan: an estimate of the
    hospital's part, not a price Anthem filed. None when the code has no OPPS rate."""
    amount = opps_payment(conn, billing_code)
    if amount is None:
        return None
    return {"amount": amount, "source": "medicare_opps", "kind": "estimate"}


@router.get("/imaging/providers")
def imaging_providers(
    billing_code: str,
    network_name: Optional[str] = None,
    zip_code: Optional[str] = Query(default=None, alias="zip", pattern=r"^\d{5}$"),
    radius_mi: Optional[float] = Query(default=None, gt=0, le=500),
    limit: int = Query(default=40, le=200),
):
    """Practices that do imaging, priced for `billing_code` on `network_name`,
    cheapest complete price first. A rate reaches every NPI on a billing
    group's roster, so an imaging-taxonomy NPI on the roster does not make the
    practice an imaging practice. A practice counts when its own billing NPI is
    an imaging clinic/center, or it has an imaging-taxonomy provider who billed
    Medicare for this scan (CMS utilization, `evidence`). A practice's own rates differ by contract, so each component
    carries `min` / `typical` (the median) / `max`. `basis` is `global`
    (one all-in rate), `split` (technical + professional, summed), or
    `professional_only` / `technical_only` (a part of the price; ranked after
    every complete one). `zip` adds `distance_mi` and `city` of the nearest
    imaging provider; `radius_mi` drops practices farther away or without
    coordinates. Only price lines that apply in an office (place of service 11)
    count. Plan-specific rates win over shared ones within a practice and
    component (AGENTS.md rule 5)."""
    if billing_code not in SERVICE_LINE_BILLING_CODES["imaging"]:
        raise HTTPException(400, f"not an imaging menu code: {billing_code!r}")
    if not network_name:
        raise HTTPException(400, detail={"code": "network_required",
                                         "message": "Pick your plan to see imaging prices."})
    if radius_mi is not None and not zip_code:
        raise HTTPException(400, "radius_mi needs zip")
    conn = db()
    here = None
    if zip_code:
        here = conn.execute(f"SELECT lat, lon FROM {ZIP_CENTROIDS_SRC} WHERE zip = ?", [zip_code]).fetchone()
        if not here:
            raise HTTPException(400, f"unknown zip: {zip_code}")

    where, params = rate_filters(billing_code, "CPT", network_name, None, None, drop_sentinel=True)
    dist = (f"CASE WHEN pd.lat IS NULL THEN NULL ELSE "
            f"{haversine_sql('pd.lat', 'pd.lon', repr(float(here[0])), repr(float(here[1])))} END"
            if here else "NULL")
    rows = conn.execute(f"""
        WITH bn AS (
            SELECT DISTINCT npi FROM {EVIDENCE_SRC}
            WHERE tier = 'billed' AND billing_code = ?
        ),
        j AS (
            SELECT gm.tin_value, gm.npi, gm.npi IN (SELECT npi FROM bn) AS billed,
                   COALESCE(pg.modifier, '') AS comp,
                   pg.negotiated_rate AS rate, pg.source_kind,
                   pd.city, pd.entity_type, pd.name, pd.org_name, {dist} AS dist
            FROM {RATE_GROUPS_SRC} pg
            JOIN {GROUP_MEMBERS_SRC} gm
              ON gm.file_id = pg.file_id AND gm.provider_group_id = pg.provider_group_id
            JOIN {PROVIDER_DIM_SRC} pd ON pd.npi = gm.npi
            WHERE {where}
              AND COALESCE(pg.modifier, '') IN ('', 'TC', '26')
              AND {OFFICE_POS}
              AND CONTAINS(',' || COALESCE(pd.service_lines, '') || ',', ',imaging,')
        ),
        ruled AS (
            SELECT *, BOOL_OR(source_kind = 'plan_specific') OVER (PARTITION BY tin_value, comp) AS any_plan
            FROM j
        ),
        comp AS (
            SELECT tin_value, comp, MIN(rate) AS lo, MEDIAN(rate) AS med, MAX(rate) AS hi
            FROM ruled WHERE NOT any_plan OR source_kind = 'plan_specific'
            GROUP BY 1, 2
        ),
        loc AS (
            SELECT tin_value, COUNT(DISTINCT npi) AS n_providers, BOOL_OR(billed) AS any_billed, MIN(dist) AS dist,
                   ARG_MIN(city, dist) FILTER (WHERE dist IS NOT NULL) AS near_city,
                   MODE(city) AS city,
                   ANY_VALUE(org_name) FILTER (WHERE org_name IS NOT NULL AND org_name <> '') AS member_org,
                   LIST(DISTINCT name) FILTER (WHERE entity_type = 'individual' AND name <> '') AS indiv
            FROM j GROUP BY 1
        )
        SELECT l.tin_value, l.n_providers, l.dist, COALESCE(l.near_city, l.city), l.member_org,
               l.indiv[1:3], tn.org_name,
               MAX(c.lo)  FILTER (WHERE c.comp = '')   , MAX(c.med) FILTER (WHERE c.comp = ''),
               MAX(c.hi)  FILTER (WHERE c.comp = '')   ,
               MAX(c.lo)  FILTER (WHERE c.comp = 'TC') , MAX(c.med) FILTER (WHERE c.comp = 'TC'),
               MAX(c.hi)  FILTER (WHERE c.comp = 'TC') ,
               MAX(c.lo)  FILTER (WHERE c.comp = '26') , MAX(c.med) FILTER (WHERE c.comp = '26'),
               MAX(c.hi)  FILTER (WHERE c.comp = '26')
        FROM loc l
        JOIN comp c USING (tin_value)
        LEFT JOIN {PROVIDER_DIM_SRC} tn ON tn.npi = TRY_CAST(l.tin_value AS BIGINT)
        WHERE l.any_billed OR CONTAINS(',' || COALESCE(tn.service_lines, '') || ',', ',imaging,')
        GROUP BY l.tin_value, l.n_providers, l.dist, COALESCE(l.near_city, l.city), l.member_org, l.indiv, tn.org_name
    """, [billing_code] + params).fetchall()

    results = []
    for (tin, n, dist_mi, city, member_org, indiv, tin_org, gl, gm, gh, tl, tm, th, pl, pm, ph) in rows:
        if radius_mi is not None and (dist_mi is None or dist_mi > radius_mi):
            continue
        glob, tech, pro = _stats(gl, gm, gh), _stats(tl, tm, th), _stats(pl, pm, ph)
        basis, typical = _price(glob, tech, pro)
        results.append({
            "practice_id": tin,
            "practice_name": tin_org or member_org,
            "ga_indiv_names": indiv or [],
            "city": city,
            "distance_mi": round(dist_mi, 1) if dist_mi is not None else None,
            "n_providers": n,
            "basis": basis,
            "typical_rate": typical,
            "global": glob, "technical": tech, "professional": pro,
        })
    complete = ("global", "split")
    results.sort(key=lambda r: (r["basis"] not in complete, r["typical_rate"], r["distance_mi"] is None,
                                r["distance_mi"] or 0, r["practice_name"] or "", r["practice_id"]))
    # Same-named TINs fold into the first (cheapest, then nearest) row.
    merged, seen = [], {}
    for r in results:
        key = _same_practice(r["practice_name"]) if r["practice_name"] else r["practice_id"]
        if key in seen:
            seen[key]["n_providers"] = max(seen[key]["n_providers"], r["n_providers"])
            continue
        seen[key] = r
        merged.append(r)
    results = merged
    return {
        "billing_code": billing_code,
        "network_name": network_name,
        "n_practices": len(results),
        "facility_estimate": _facility_estimate(conn, billing_code),
        "results": results[:limit],
    }
