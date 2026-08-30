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
    RATE_HIST_PATH,
    db,
    have_rate_hist,
    network_slug,
    outpatient_scope,
    price_filters,
)
from ..labels import (
    MODIFIER_LABELS,
    POS_LABELS,
    plausibility,
    pos_bucket,
    provider_card,
)
from ..evidence import code_tiers, did_bill, medicare_specialty

router = APIRouter()

_HIST_HAS_SCOPE: Optional[bool] = None


def _hist_has_scope(conn) -> bool:
    """Whether rate_hist.parquet carries the `scope` column (builds since the
    outpatient-scope change). An older summary without it: fall back to the
    unscoped overview rather than 500."""
    global _HIST_HAS_SCOPE
    if _HIST_HAS_SCOPE is None:
        try:
            cols = {r[0] for r in conn.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{RATE_HIST_PATH}')").fetchall()}
            _HIST_HAS_SCOPE = "scope" in cols
        except Exception:
            _HIST_HAS_SCOPE = False
    return _HIST_HAS_SCOPE


def _overview_from_summary(conn, billing_code_type, network_name, setting):
    """Network overview from summary/rate_hist.parquet — no `prices` scan.

    `distribution` = the histogram bars (rate entries per $50 band, capped at
    $2000 to match the code-level view). `summary` = volume-weighted min / median
    / avg / max of every negotiated rate in scope, off the pooled CDF (≤201
    buckets — trivial). `n_codes` replaces `provider_groups` (not derivable here
    without `prices ⨝ group_sets` — #48).
    """
    hsrc = f"read_parquet('{RATE_HIST_PATH}')"
    conds, hp = ["billing_code_type = ?"], [billing_code_type]
    if _hist_has_scope(conn):
        # outpatient professional fee-for-service only — matches the drill-downs
        conds.append("scope = 'outpatient_prof'")
    if network_name:
        conds.append("net = ?")
        hp.append(network_slug(network_name))
    if setting in ("outpatient", "both"):
        conds.append("setting = ?")
        hp.append(setting)
    hwhere = " AND ".join(conds)

    dist = conn.execute(f"""
        SELECT FLOOR(LEAST(bucket, 2000) / 50) * 50 AS rate,
               'fee schedule' AS negotiated_type,
               SUM(n) AS provider_groups
        FROM {hsrc}
        WHERE {hwhere}
        GROUP BY 1, 2
        ORDER BY 1
    """, hp).fetchall()

    if not dist:
        raise HTTPException(404, detail=f"No {billing_code_type} rates found for this network")

    srow = conn.execute(f"""
        WITH b AS (
            SELECT bucket, SUM(n) AS n
            FROM {hsrc} WHERE {hwhere}
            GROUP BY 1
        ),
        c AS (
            SELECT bucket, SUM(n) OVER (ORDER BY bucket) AS cum, SUM(n) OVER () AS tot
            FROM b
        )
        SELECT
            (SELECT MIN(bucket) FROM b),
            (SELECT MAX(bucket) FROM b),
            (SELECT SUM(bucket * n)::DOUBLE / NULLIF(SUM(n), 0) FROM b),
            (SELECT MIN(bucket) FROM c WHERE cum >= tot / 2.0),
            (SELECT COUNT(DISTINCT billing_code) FROM {hsrc} WHERE {hwhere}),
            (SELECT SUM(n) FROM b)
    """, hp + hp).fetchone()

    return {
        "billing_code": "ALL",
        "billing_code_type": "NETWORK",
        "summary": {
            "min":    round(srow[0], 2) if srow[0] is not None else None,
            "max":    round(srow[1], 2) if srow[1] is not None else None,
            "max_capped": srow[1] is not None and srow[1] >= 5000,  # overflow bucket
            "avg":    round(srow[2], 2) if srow[2] is not None else None,
            "median": round(srow[3], 2) if srow[3] is not None else None,
            "provider_groups": None,   # not derivable without prices ⨝ group_sets — #48
            "n_providers":     None,
            "n_codes":       srow[4] or 0,
            "total_entries": int(srow[5]) if srow[5] is not None else 0,
        },
        "distribution": [
            {"rate": r[0], "type": r[1], "provider_groups": int(r[2])} for r in dist
        ],
    }


@router.get("/rates/distribution")
def rate_distribution(
    billing_code: Optional[str] = None,
    billing_code_type: str = "CPT",
    plan_name: Optional[str] = None,  # accepted for API compat, unused
    network_name: Optional[str] = None,
    setting: Optional[str] = None,
    npi: Optional[int] = None,
    specialty: Optional[str] = None,
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

    # ── Network overview (no code): read the precomputed histogram, never
    # `prices` (645M rows → OOM at GA scale, issue #10). The bars ARE the
    # histogram; the summary is a volume-weighted CDF over CPT only, so a $0.01
    # revenue-code line and a $7M drug-unit outlier don't blow it up (#51).
    if not billing_code and have_rate_hist():
        return _overview_from_summary(conn, billing_code_type, network_name, setting)

    # Expanding prices → provider groups is only affordable when the filter
    # prunes prices hard (a billing_code) or the query needs per-NPI resolution
    # (an npi filter). The bare overview (no code, maybe a network) aggregates
    # over `prices` alone — bars/counts are distinct provider *rosters*, not the
    # fully-expanded group count.
    heavy = bool(billing_code or npi)

    if heavy:
        # specialty scopes to groups containing a provider of that specialty —
        # only affordable here, where billing_code/npi has already pruned prices.
        where, params = price_filters(billing_code, billing_code_type, network_name,
                                      setting, npi, specialty=specialty)
        src = f"{PRICE_GROUPS_SRC} pg"
        grp = "COUNT(DISTINCT (pg.file_id, pg.provider_group_id))"
    else:
        # prices-only: reuse price_filters minus the npi branch (npi ⇒ heavy).
        # specialty is ignored on the bare overview (no code to prune the fanout).
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
    conds = ["pg.billing_code = ?", "pg.billing_code_type = ?", "COALESCE(pg.modifier,'') = ''",
             outpatient_scope("pg")]
    params: list = [billing_code, billing_code_type]
    if setting in ("outpatient", "both"):
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
        ),
        -- distinct NPIs per network for this code. Bounded (one billing code,
        -- per_group is already tiny) so the providers join is cheap here even
        -- though the same join over the whole store is not.
        net_npi AS (
            SELECT DISTINCT g.network_name, pv.npi
            FROM per_group g
            JOIN {PROVIDERS_SRC} pv
              ON pv.file_id = g.file_id AND pv.provider_group_id = g.provider_group_id
        ),
        net_counts AS (
            SELECT network_name, COUNT(*) AS n_providers FROM net_npi GROUP BY 1
        )
        SELECT pg.network_name,
               MIN(pg.lo), MAX(pg.hi), MEDIAN(pg.med),
               -- 10th/90th percentile of per-group medians: the spread a patient
               -- realistically sees, ignoring a handful of $0.09 / $19k outliers
               QUANTILE_CONT(pg.med, 0.1), QUANTILE_CONT(pg.med, 0.9),
               COUNT(*) AS n_groups,
               ANY_VALUE(nc.n_providers) AS n_providers
        FROM per_group pg
        LEFT JOIN net_counts nc ON nc.network_name = pg.network_name
        GROUP BY 1
        ORDER BY MEDIAN(pg.med)
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
            "n_providers": r[7],
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
    specialty: Optional[str] = None,
    ga_hospitals_only: bool = False,
    component: str = "global",
    sort: str = "rate_asc",
    limit: int = Query(default=200, le=1000),
):
    """
    "Compare across providers" — one row per **billing practice** (the group's
    `tin_value`, an org NPI that resolves to a real practice name), ordered by
    price. Defaults to the global (unmodified) rate so practices compare
    like-for-like; `component=all` keeps every modifier, or pass a specific one
    ("26", "TC").

    A practice recurs across the MRF as many file-local `provider_reference`
    groups; folding on `tin_value` collapses those to one row (issue #48). Per-
    row `n_groups` counts the groups a practice's rate reaches you through, so
    it can exceed the summary's distinct `n_groups` (a group spanning several
    TINs counts once per practice). `summary` min/median/max is over the
    group-rate distribution — supports a headline like "most contracts ~$82".

    One heavy pass materialises `_prac` (prices ⨝ group_sets ⨝ providers, pruned
    to one code + the outpatient scope); the rest aggregate that temp table. The
    NPPES name lookup runs only for the practices actually returned.
    """
    conn = db()

    where, params = price_filters(billing_code, billing_code_type, network_name,
                                  setting, npi, specialty=specialty)
    if component == "global":
        # NULL = a file parsed before the modifier column existed; treat as global.
        where += " AND COALESCE(pg.modifier, '') = ''"
    elif component != "all":
        where += " AND pg.modifier = ?"
        params = params + [component]
    has_nppes = os.path.exists(GA_NPPES_PATH)

    ga_join = ga_cols = ""
    if has_nppes:
        ga_join = f"LEFT JOIN read_parquet('{GA_NPPES_PATH}') ga ON ga.npi = p.npi"
        ga_cols = (", COALESCE(ga.is_hospital, FALSE) AS is_hospital,"
                   "  COALESCE(ga.is_clinic, FALSE)   AS is_clinic")

    conn.execute(f"""
        CREATE TEMP TABLE _prac AS
        SELECT p.tin_value, pg.network_name, pg.file_id, pg.provider_group_id,
               pg.negotiated_rate, pg.negotiated_type, p.npi{ga_cols}
        FROM {PRICE_GROUPS_SRC} pg
        JOIN {PROVIDERS_SRC} p
          ON p.file_id = pg.file_id AND p.provider_group_id = pg.provider_group_id
        {ga_join}
        WHERE {where}
    """, params)

    try:
        summary = conn.execute("""
            WITH pg_rate AS (
                SELECT DISTINCT tin_value, network_name, file_id, provider_group_id,
                                negotiated_rate
                FROM _prac
            )
            SELECT MIN(negotiated_rate), MAX(negotiated_rate),
                   AVG(negotiated_rate), MEDIAN(negotiated_rate),
                   COUNT(*),
                   COUNT(DISTINCT (file_id, provider_group_id)),
                   (SELECT COUNT(DISTINCT npi) FROM _prac)
            FROM pg_rate
        """).fetchone()

        hosp_cols = (", COUNT(DISTINCT npi) FILTER (WHERE is_hospital) AS hosp_npis,"
                     "  COUNT(DISTINCT npi) FILTER (WHERE is_clinic)   AS clinic_npis") if has_nppes else ""
        hosp_sel = ", COALESCE(n.hosp_npis, 0), COALESCE(n.clinic_npis, 0)" if has_nppes else ""
        tin_rows = conn.execute(f"""
            WITH pg_rate AS (
                SELECT DISTINCT tin_value, network_name, file_id, provider_group_id,
                                negotiated_rate, negotiated_type
                FROM _prac
            ),
            rate_lvl AS (
                SELECT tin_value, network_name,
                       MIN(negotiated_rate) AS mn, MAX(negotiated_rate) AS mx,
                       MEDIAN(negotiated_rate) AS md, ANY_VALUE(negotiated_type) AS nt,
                       COUNT(DISTINCT (file_id, provider_group_id)) AS n_groups
                FROM pg_rate GROUP BY 1, 2
            ),
            npi_lvl AS (
                SELECT tin_value, network_name, COUNT(DISTINCT npi) AS npi_count{hosp_cols}
                FROM _prac GROUP BY 1, 2
            )
            SELECT r.tin_value, r.network_name, r.mn, r.mx, r.md, r.nt, r.n_groups,
                   COALESCE(n.npi_count, 0){hosp_sel}
            FROM rate_lvl r
            LEFT JOIN npi_lvl n USING (tin_value, network_name)
        """).fetchall()
    finally:
        conn.execute("DROP TABLE _prac")

    if not tin_rows:
        raise HTTPException(404, detail=f"No rates for {billing_code_type}:{billing_code}")

    # dict per practice: (mn, mx, md, nt, n_groups, npi_count[, hosp, clinic])
    practices = []
    for t in tin_rows:
        d = {"practice_id": t[0], "network_name": t[1], "mn": t[2], "mx": t[3],
             "md": t[4], "nt": t[5], "n_groups": t[6], "npi_count": t[7]}
        if has_nppes:
            d["hosp"], d["clinic"] = t[8], t[9]
        practices.append(d)

    if ga_hospitals_only:
        practices = [p for p in practices if p.get("hosp", 0) > 0]

    practices.sort(key=lambda p: (-p["mx"], -p["mn"]) if sort == "rate_desc"
                   else (p["mn"], p["mx"]))
    shown = practices[:limit]

    # ── name the practices actually shown: the TIN's own org NPI, plus the org /
    # individual names of its member providers. One bounded scan of `providers`.
    names: dict = {}
    if has_nppes and shown:
        ids = [p["practice_id"] for p in shown]
        ph = ", ".join("?" * len(ids))
        for e in conn.execute(f"""
            SELECT p.tin_value,
                   ANY_VALUE(tn.org_name) FILTER (WHERE tn.org_name IS NOT NULL AND tn.org_name != ''),
                   LIST(DISTINCT ga.org_name) FILTER (WHERE ga.org_name IS NOT NULL AND ga.org_name != ''),
                   LIST(DISTINCT TRIM(BOTH ', ' FROM ga.last_name || ', ' || ga.first_name))
                     FILTER (WHERE ga.entity_type = 'individual' AND ga.last_name IS NOT NULL AND ga.last_name != ''),
                   LIST(DISTINCT ga.taxonomy_group) FILTER (WHERE ga.taxonomy_group IS NOT NULL AND ga.taxonomy_group != '')
            FROM {PROVIDERS_SRC} p
            LEFT JOIN read_parquet('{GA_NPPES_PATH}') ga ON ga.npi = p.npi
            LEFT JOIN read_parquet('{GA_NPPES_PATH}') tn ON tn.npi = TRY_CAST(p.tin_value AS BIGINT)
            WHERE p.tin_value IN ({ph})
            GROUP BY p.tin_value
        """, ids).fetchall():
            names[e[0]] = e

    def row(p):
        d = {
            "practice_id":     p["practice_id"],
            "practice_name":   None,
            "min_rate":        round(p["mn"], 2) if p["mn"] is not None else None,
            "max_rate":        round(p["mx"], 2) if p["mx"] is not None else None,
            "median_rate":     round(p["md"], 2) if p["md"] is not None else None,
            "negotiated_rate": round(p["mn"], 2) if p["mn"] is not None else None,  # back-compat
            "negotiated_type": p["nt"],
            "network_name":    p["network_name"],
            "npi_count":       p["npi_count"],
            "n_groups":        p["n_groups"],
        }
        if has_nppes:
            e = names.get(p["practice_id"])
            orgs = (e[2] if e else None) or []
            indiv = (e[3] if e else None) or []
            d["practice_name"] = (e[1] if e else None) or (orgs[0] if orgs else None)
            d["ga_hospital_npis"] = p["hosp"]
            d["ga_clinic_npis"] = p["clinic"]
            d["ga_org_names"] = orgs[:5]
            d["ga_indiv_names"] = indiv[:5]
            d["ga_taxonomies"] = ((e[4] if e else None) or [])[:4]
        return d

    return {
        "billing_code":      billing_code,
        "billing_code_type": billing_code_type,
        "component":         component,
        "nppes_ga": has_nppes,
        "summary": {
            "min":         round(summary[0], 2) if summary[0] is not None else None,
            "max":         round(summary[1], 2) if summary[1] is not None else None,
            "avg":         round(summary[2], 2) if summary[2] is not None else None,
            "median":      round(summary[3], 2) if summary[3] is not None else None,
            "n_rows":      summary[4] or 0,
            "n_groups":    summary[5] or 0,
            "n_providers": summary[6] or 0,
            "n_practices": len(practices),
        },
        "results": [row(p) for p in shown],
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
          AND {outpatient_scope("p")}
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

    # Medicare Part B evidence (issue #14). `util` and `med_spec` are None until
    # `make cms-utilization` has run.
    util = did_bill(conn, npi, billing_code)
    med_spec = medicare_specialty(conn, npi)

    plaus = None
    if card and os.path.exists(CODE_LABELS_PATH):
        lab = conn.execute(f"""
            SELECT rbcs_category, rbcs_family
            FROM read_parquet('{CODE_LABELS_PATH}')
            WHERE billing_code = ? AND billing_code_type = ? LIMIT 1
        """, [billing_code, billing_code_type]).fetchone()
        if lab:
            plaus = plausibility(card.get("_grouping"), card.get("_classification"),
                                 card.get("specialty"), lab[0], lab[1],
                                 medicare_type=med_spec)

    # When the provider demonstrably bills this code, the "group's rate, not the
    # individual's" framing that a weak cross-specialty heuristic triggers is
    # actively misleading — demote it.
    if util and util.get("billed") and plaus == "unlikely":
        plaus = "typical"

    # Confidence tier for this (provider, code): billed > typical-for-specialty >
    # group (the rate only reaches them via a shared billing group).
    tier = code_tiers(conn, npi, card.get("_classification") if card else None,
                      [billing_code]).get(billing_code, "group") if card else None

    if card:
        card.pop("_grouping", None)
        card.pop("_classification", None)

    return {
        "billing_code": billing_code,
        "billing_code_type": billing_code_type,
        "npi": npi,
        "network_name": network_name,
        "tier": tier,
        "provider": card,
        "plausibility": plaus,
        "medicare_utilization": util,
        "headline": headline,
        "components": components,
        "is_component_split": split,
    }
