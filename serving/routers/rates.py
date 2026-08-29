"""Rate endpoints — the four consumer jobs plus the histogram.

  /rates/distribution  histogram (400s on npi-without-code — that view is the menu)
  /rates/by_network    job 2 — a procedure priced across every network
  /rates/providers     job 3 — compare across provider groups
  /rates/quote         job 1 — one procedure at one provider
"""
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..data_sources import (
    GA_NPPES_PATH,
    CODE_LABELS_PATH,
    PRICE_GROUPS_SRC,
    PRICES_SRC,
    PROVIDERS_SRC,
    GROUP_SETS_SRC,
    db,
    network_slug,
    price_filters,
)
from ..labels import (
    MODIFIER_LABELS,
    POS_LABELS,
    plausibility,
    pos_bucket,
    provider_card,
)
from ..evidence import did_bill

router = APIRouter()


@router.get("/rates/distribution")
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
        where, params = price_filters(billing_code, billing_code_type, network_name, setting, npi)
        src = f"{PRICE_GROUPS_SRC} pg"
        grp = "COUNT(DISTINCT (pg.file_id, pg.provider_group_id))"
    else:
        # prices-only: reuse price_filters minus the npi branch (npi ⇒ heavy).
        where, params = price_filters(None, billing_code_type, network_name, setting, None)
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


@router.get("/rates/by_network")
def rates_by_network(
    billing_code: str,
    billing_code_type: str = "CPT",
    setting: Optional[str] = None,
):
    """Job 2 — same procedure, every network side by side. One row per network
    that carries the code: the global (unmodified) rate's spread + how much it
    varies by provider (n_distinct_rates). Answers "does my plan choice matter
    for this procedure" — a tight fee-schedule HMO vs. a wide-spread PPO."""
    conn = db()
    conds = ["pg.billing_code = ?", "pg.billing_code_type = ?", "COALESCE(pg.modifier,'') = ''"]
    params: list = [billing_code, billing_code_type]
    if setting:
        conds.append("pg.setting = ?")
        params.append(setting)

    rows = conn.execute(f"""
        WITH per_group AS (
            SELECT pg.network_name, pg.file_id, pg.provider_group_id,
                   MIN(pg.negotiated_rate) AS lo, MAX(pg.negotiated_rate) AS hi,
                   MEDIAN(pg.negotiated_rate) AS med
            FROM {PRICE_GROUPS_SRC} pg
            WHERE {" AND ".join(conds)}
            GROUP BY 1, 2, 3
        )
        SELECT network_name,
               MIN(lo), MAX(hi), MEDIAN(med),
               -- 10th/90th percentile of per-group medians: the spread a patient
               -- realistically sees, ignoring a handful of $0.09 / $19k outliers
               QUANTILE_CONT(med, 0.1), QUANTILE_CONT(med, 0.9),
               COUNT(*) AS n_groups
        FROM per_group
        GROUP BY 1
        ORDER BY MEDIAN(med)
    """, params).fetchall()

    if not rows:
        raise HTTPException(404, detail=f"No rates for {billing_code_type}:{billing_code} in any network")

    def block(r):
        p10, p90 = r[4], r[5]
        spread = round(p90 / p10, 1) if p10 and p10 > 0 else None
        return {
            "network_name": r[0],
            "min": round(r[1], 2), "max": round(r[2], 2), "median": round(r[3], 2),
            "typical_low": round(p10, 2), "typical_high": round(p90, 2),
            "n_groups": r[6],
            "spread": spread,  # p90/p10 of per-group medians; ~1 = flat, >3 = provider matters a lot
        }

    return {
        "billing_code": billing_code,
        "billing_code_type": billing_code_type,
        "networks": [block(r) for r in rows],
    }


@router.get("/rates/providers")
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

    where, params = price_filters(billing_code, billing_code_type, network_name, setting, npi)
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


@router.get("/rates/quote")
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
        bucket = pos_bucket(service_code)
        c = comps.setdefault(mod, {})
        s = c.setdefault(bucket, {"min": lo, "max": hi, "n": 0, "negotiated_type": ntype})
        s["min"] = min(s["min"], lo)
        s["max"] = max(s["max"], hi)
        s["n"] += n

    def comp_block(mod):
        label, desc = MODIFIER_LABELS.get(mod, (f"Modifier {mod}", ""))
        settings = sorted(
            (
                {
                    "pos_bucket": b,
                    "pos_label": POS_LABELS.get(b, b),
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
    card = provider_card(conn, npi)

    plaus = None
    if card and os.path.exists(CODE_LABELS_PATH):
        lab = conn.execute(f"""
            SELECT rbcs_category, rbcs_family
            FROM read_parquet('{CODE_LABELS_PATH}')
            WHERE billing_code = ? AND billing_code_type = ? LIMIT 1
        """, [billing_code, billing_code_type]).fetchone()
        if lab:
            plaus = plausibility(card.get("_grouping"), card.get("_classification"),
                                 card.get("specialty"), lab[0], lab[1])

    # Medicare Part B evidence (issue #14). When the provider demonstrably bills
    # this code, the "group's rate, not the individual's" framing that a weak
    # cross-specialty heuristic triggers is actively misleading — demote it.
    # `util` is None until `make cms-utilization` has run.
    util = did_bill(conn, npi, billing_code)
    if util and util.get("billed") and plaus == "unlikely":
        plaus = "typical"

    if card:
        card.pop("_grouping", None)
        card.pop("_classification", None)

    return {
        "billing_code": billing_code,
        "billing_code_type": billing_code_type,
        "npi": npi,
        "network_name": network_name,
        "provider": card,
        "plausibility": plaus,
        "medicare_utilization": util,
        "headline": headline,
        "components": components,
        "is_component_split": split,
    }
