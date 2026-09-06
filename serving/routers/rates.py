"""Rate endpoints — the four consumer jobs plus the histogram.

  /rates/distribution  histogram (400s on npi-without-code — that view is the menu)
  /rates/by_network    job 2 — a procedure priced across every network
  /rates/providers     job 3 — compare across provider groups
  /rates/quote         job 1 — one procedure at one provider
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..data_sources import (
    CODE_DIM_SRC,
    GROUP_MEMBERS_SRC,
    GROUP_SETS_SRC,
    PROVIDER_DIM_SRC,
    RATE_GROUPS_SRC,
    RATE_HIST_SRC,
    RATES_SRC,
    ROLLUP_SRC,
    db,
    network_slug,
    rate_filters,
)
from ..labels import (
    MODIFIER_LABELS,
    POS_LABELS,
    plausibility,
    pos_bucket,
    provider_card,
    strip_private,
)
from ..evidence import code_tiers, did_bill, medicare_specialty
from ..benchmark import medicare_allowed as _medicare_allowed

router = APIRouter()


def _dist_from_hist(conn, billing_code, billing_code_type, network_name, setting):
    """Rate distribution off `rate_hist` — no `rates` scan. Serves the no-code
    network overview *and* the code-level view when no `network_name` prunes the
    live path (issue #10 — the unpruned `rates ⨝ group_sets` expansion spills
    15-60 GB at GA scale).

    `distribution` = the histogram bars (rate entries per $50 band). `summary` =
    volume-weighted min/median/avg/max off the pooled CDF. Per-group /
    per-provider counts aren't derivable here (no `group_sets` join) — they
    come back `null`, with `n_codes` on the overview. Sentinel (placeholder)
    rows are excluded, same as the live path.
    """
    conds, hp = ["billing_code_type = ?", "NOT is_sentinel"], [billing_code_type]
    conds.append("scope = 'outpatient_prof'")
    if billing_code:
        conds.append("billing_code = ?")
        hp.append(billing_code)
    if network_name:
        conds.append("net = ?")
        hp.append(network_slug(network_name))
    if setting in ("outpatient", "both"):
        conds.append("setting = ?")
        hp.append(setting)
    hwhere = " AND ".join(conds)

    bar_cap = 5000 if billing_code else 2000
    dist = conn.execute(f"""
        SELECT FLOOR(LEAST(bucket, {bar_cap}) / 50) * 50 AS rate,
               'fee schedule' AS negotiated_type,
               SUM(n) AS provider_groups
        FROM {RATE_HIST_SRC}
        WHERE {hwhere}
        GROUP BY 1, 2
        ORDER BY 1
    """, hp).fetchall()

    if not dist:
        label = f"{billing_code_type}:{billing_code}" if billing_code else billing_code_type
        raise HTTPException(404, detail=f"No rates found for {label}")

    srow = conn.execute(f"""
        WITH b AS (
            SELECT bucket, SUM(n) AS n
            FROM {RATE_HIST_SRC} WHERE {hwhere}
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
            (SELECT COUNT(DISTINCT billing_code) FROM {RATE_HIST_SRC} WHERE {hwhere}),
            (SELECT SUM(n) FROM b)
    """, hp + hp).fetchone()

    return {
        "billing_code": billing_code or "ALL",
        "billing_code_type": billing_code_type if billing_code else "NETWORK",
        "summary": {
            "min":    round(srow[0], 2) if srow[0] is not None else None,
            "max":    round(srow[1], 2) if srow[1] is not None else None,
            "max_capped": srow[1] is not None and srow[1] >= 5000,  # overflow bucket
            "avg":    round(srow[2], 2) if srow[2] is not None else None,
            "median": round(srow[3], 2) if srow[3] is not None else None,
            "provider_groups": None,   # not derivable without group_sets — #48
            "n_providers":     None,
            "n_codes":       None if billing_code else (srow[4] or 0),
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
    # An npi filter with no billing_code would full-scan rates (nothing prunes
    # the code axis) — it hangs. That view is the provider "menu": use
    # /providers/{npi}/procedures instead.
    if npi and not billing_code:
        raise HTTPException(
            400,
            detail="Select a procedure to see its rate distribution for this provider, "
                   "or call /providers/{npi}/procedures for the full menu.",
        )

    conn = db()

    # ── Read the precomputed histogram, never `rates`, whenever nothing prunes
    # the live path OR the query is a browse-level overview: any no-code view
    # (network-scoped or not), and the code-level view without a `network_name`
    # (the unpruned `rates ⨝ group_sets` expansion spills 15-60 GB at GA scale —
    # issue #10). rate_hist is Hive-partitioned by network and its buckets cap at
    # $5k, so a network overview off it is both fast and immune to the
    # million-dollar HCPCS drug rates (gene therapies, biologics — priced per
    # course, not shoppable) that otherwise blow out the live min/max/avg. The
    # live path runs only for a code+network drill-down or an `npi` anchor.
    if not billing_code or not (network_name or npi):
        return _dist_from_hist(conn, billing_code, billing_code_type, network_name, setting)

    # Expanding rates → provider groups is only affordable when the filter
    # prunes rates hard (a billing_code + network) or the query needs per-NPI
    # resolution (an npi filter) — always true here (the branch above handled
    # everything else).
    where, params = rate_filters(billing_code, billing_code_type, network_name,
                                 setting, npi, specialty=specialty, drop_sentinel=True)
    src = f"{RATE_GROUPS_SRC} pg"
    grp = "COUNT(DISTINCT (pg.file_id, pg.provider_group_id))"

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
        label = f"{billing_code_type}:{billing_code}"
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

    n_providers = conn.execute(f"""
        SELECT COUNT(DISTINCT gm.npi)
        FROM {src}
        JOIN {GROUP_MEMBERS_SRC} gm
          ON gm.file_id = pg.file_id AND gm.provider_group_id = pg.provider_group_id
        WHERE {where}
    """, params).fetchone()[0]

    return {
        "billing_code":      billing_code,
        "billing_code_type": billing_code_type,
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
    that carries the code, off the precomputed `cross_network_rollup` (a
    roster-weighted CDF over `rate_hist` — global modifier, outpatient-prof,
    non-sentinel). Answers "does my plan choice matter for this procedure" — a
    tight fee-schedule HMO vs. a wide-spread PPO.

    `setting` narrows to a rate_hist bucket that isn't 'outpatient'/'both' by
    falling back to the live path (rare; the rollup is unconditioned on setting
    beyond the scope's own outpatient/both requirement)."""
    conn = db()

    if setting not in (None, "", "outpatient", "both"):
        setting = None  # can't narrow an outpatient-scoped view — see rate_filters

    rows = conn.execute(f"""
        SELECT network_name, min_rate, max_rate, median, p10, p90, n_groups
        FROM {ROLLUP_SRC}
        WHERE billing_code = ? AND billing_code_type = ?
        ORDER BY median
    """, [billing_code, billing_code_type]).fetchall()

    if not rows:
        raise HTTPException(404, detail=f"No rates for {billing_code_type}:{billing_code} in any network")

    def block(r):
        p10, p90 = r[4], r[5]
        spread = round(p90 / p10, 1) if p10 and p10 > 0 else None
        return {
            "network_name": r[0],
            "min": r[1], "max": r[2], "median": r[3],
            "typical_low": p10, "typical_high": p90,
            "n_groups": r[6],
            "n_providers": None,  # not derivable from the rollup — was a live count
            "spread": spread,  # p90/p10; ~1 = flat, >3 = provider matters a lot
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
    `tin_value`, an org NPI that resolves to a name via `provider_dim`), ordered
    by price. Defaults to the global (unmodified) rate so practices compare
    like-for-like; `component=all` keeps every modifier, or pass a specific one
    ("26", "TC").

    A practice recurs across the MRF as many file-local `provider_group_id`s;
    folding on `tin_value` collapses those to one row (issue #48). **Rule 5**
    (AGENTS.md #5, #100): within each practice, a `plan_specific` row wins over
    a `shared` one for the same code — `provider_group_id` is file-local, so
    this never collapses across files by that id, only within one already-
    grouped practice's rows. Per-row `n_groups` counts the groups a practice's
    rate reaches you through, so it can exceed the summary's distinct
    `n_groups`. `summary` min/median/max is over the representative-rate
    distribution — supports a headline like "most contracts ~$82".

    One heavy pass materialises `_prac` (rates ⨝ group_sets ⨝ group_members,
    pruned to one code + one network + the outpatient scope, sentinel dropped);
    the rest aggregate that temp table. The name lookup runs only for the
    practices actually returned.

    **A `network_name` is required** — a practice's rate is only comparable
    within a plan, and the unpruned cross-network expansion spills 15-60 GB at
    GA scale (issue #10). The frontend gates this panel on plan selection.
    """
    if not network_name:
        raise HTTPException(
            400,
            detail={"code": "network_required",
                    "message": "Pick your plan to compare providers for this procedure."},
        )
    conn = db()

    where, params = rate_filters(billing_code, billing_code_type, network_name,
                                 setting, npi, specialty=specialty, drop_sentinel=True)
    if component == "global":
        # NULL = a file parsed before the modifier column existed; treat as global.
        where += " AND COALESCE(pg.modifier, '') = ''"
    elif component != "all":
        where += " AND pg.modifier = ?"
        params = params + [component]

    conn.execute(f"""
        CREATE TEMP TABLE _prac AS
        SELECT gm.tin_value, pg.network_name, pg.file_id, pg.provider_group_id,
               pg.negotiated_rate, pg.negotiated_type, pg.source_kind, gm.npi,
               COALESCE(pd.is_hospital, FALSE) AS is_hospital,
               COALESCE(pd.is_clinic, FALSE)   AS is_clinic
        FROM {RATE_GROUPS_SRC} pg
        JOIN {GROUP_MEMBERS_SRC} gm
          ON gm.file_id = pg.file_id AND gm.provider_group_id = pg.provider_group_id
        LEFT JOIN {PROVIDER_DIM_SRC} pd ON pd.npi = gm.npi
        WHERE {where}
    """, params)

    try:
        # `summary` stays over the FULL distinct (practice, file, group, rate)
        # distribution — rule 5 below only picks the *representative* rate
        # shown/ranked on per practice in `results`, per the #100 checkpoint
        # decision (reading (a): the minimal change, a no-op while every row is
        # still `shared`). Unchanged from before rule 5 existed.
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

        tin_rows = conn.execute("""
            WITH pg_rate AS (
                SELECT DISTINCT tin_value, network_name, file_id, provider_group_id,
                                negotiated_rate, negotiated_type, source_kind
                FROM _prac
            ),
            -- Rule 5 per practice: plan_specific rows win when present, else
            -- every shared row — then MIN/MAX/MEDIAN over whichever set applies.
            has_plan AS (
                SELECT tin_value, network_name, BOOL_OR(source_kind = 'plan_specific') AS any_plan
                FROM pg_rate GROUP BY 1, 2
            ),
            eligible AS (
                SELECT r.* FROM pg_rate r
                JOIN has_plan h USING (tin_value, network_name)
                WHERE NOT h.any_plan OR r.source_kind = 'plan_specific'
            ),
            rate_lvl AS (
                SELECT tin_value, network_name,
                       MIN(negotiated_rate) AS mn, MAX(negotiated_rate) AS mx,
                       MEDIAN(negotiated_rate) AS md, ANY_VALUE(negotiated_type) AS nt,
                       ANY_VALUE(source_kind) AS rep_kind
                FROM eligible GROUP BY 1, 2
            ),
            grp_lvl AS (
                SELECT tin_value, network_name,
                       COUNT(DISTINCT (file_id, provider_group_id)) AS n_groups
                FROM pg_rate GROUP BY 1, 2
            ),
            npi_lvl AS (
                SELECT tin_value, network_name, COUNT(DISTINCT npi) AS npi_count,
                       COUNT(DISTINCT npi) FILTER (WHERE is_hospital) AS hosp_npis,
                       COUNT(DISTINCT npi) FILTER (WHERE is_clinic)   AS clinic_npis
                FROM _prac GROUP BY 1, 2
            )
            SELECT r.tin_value, r.network_name, r.mn, r.mx, r.md, r.nt, r.rep_kind,
                   COALESCE(g.n_groups, 0), COALESCE(n.npi_count, 0),
                   COALESCE(n.hosp_npis, 0), COALESCE(n.clinic_npis, 0)
            FROM rate_lvl r
            LEFT JOIN grp_lvl g USING (tin_value, network_name)
            LEFT JOIN npi_lvl n USING (tin_value, network_name)
        """).fetchall()
    finally:
        conn.execute("DROP TABLE _prac")

    if not tin_rows:
        raise HTTPException(404, detail=f"No rates for {billing_code_type}:{billing_code}")

    practices = []
    for t in tin_rows:
        practices.append({
            "practice_id": t[0], "network_name": t[1], "mn": t[2], "mx": t[3],
            "md": t[4], "nt": t[5], "source_kind": t[6], "n_groups": t[7],
            "npi_count": t[8], "hosp": t[9], "clinic": t[10],
        })

    if ga_hospitals_only:
        practices = [p for p in practices if p.get("hosp", 0) > 0]

    practices.sort(key=lambda p: (-p["mx"], -p["mn"]) if sort == "rate_desc"
                   else (p["mn"], p["mx"]))
    shown = practices[:limit]

    # ── name the practices actually shown: the TIN's own org NPI, plus the org /
    # individual names of its member providers. One bounded scan of `provider_dim`.
    names: dict = {}
    if shown:
        ids = [p["practice_id"] for p in shown]
        ph = ", ".join("?" * len(ids))
        for e in conn.execute(f"""
            SELECT gm.tin_value,
                   ANY_VALUE(tn.org_name) FILTER (WHERE tn.org_name IS NOT NULL AND tn.org_name != ''),
                   LIST(DISTINCT pd.org_name) FILTER (WHERE pd.org_name IS NOT NULL AND pd.org_name != ''),
                   LIST(DISTINCT pd.name) FILTER (
                       WHERE pd.entity_type = 'individual' AND pd.name IS NOT NULL AND pd.name != ''),
                   LIST(DISTINCT pd.specialty) FILTER (WHERE pd.specialty IS NOT NULL AND pd.specialty != '')
            FROM {GROUP_MEMBERS_SRC} gm
            LEFT JOIN {PROVIDER_DIM_SRC} pd ON pd.npi = gm.npi
            LEFT JOIN {PROVIDER_DIM_SRC} tn ON tn.npi = TRY_CAST(gm.tin_value AS BIGINT)
            WHERE gm.tin_value IN ({ph})
            GROUP BY gm.tin_value
        """, ids).fetchall():
            names[e[0]] = e

    _NO_NAME = (None, None, [], [], [])

    def row(p):
        _, org_name, orgs, indiv, tax = names.get(p["practice_id"], _NO_NAME)
        orgs, indiv, tax = orgs or [], indiv or [], tax or []
        return {
            "practice_id":     p["practice_id"],
            "practice_name":   org_name or (orgs[0] if orgs else None),
            "min_rate":        round(p["mn"], 2) if p["mn"] is not None else None,
            "max_rate":        round(p["mx"], 2) if p["mx"] is not None else None,
            "median_rate":     round(p["md"], 2) if p["md"] is not None else None,
            "negotiated_rate": round(p["mn"], 2) if p["mn"] is not None else None,  # back-compat
            "negotiated_type": p["nt"],
            "source_kind":     p["source_kind"],
            "network_name":    p["network_name"],
            "npi_count":       p["npi_count"],
            "n_groups":        p["n_groups"],
            "ga_hospital_npis": p["hosp"],
            "ga_clinic_npis":   p["clinic"],
            "ga_org_names":     orgs[:5],
            "ga_indiv_names":   indiv[:5],
            "ga_taxonomies":    tax[:4],
        }

    return {
        "billing_code":      billing_code,
        "billing_code_type": billing_code_type,
        "component":         component,
        "nppes_ga": True,
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
    service. Returns a headline rate + the breakdown.

    **A `network_name` is required** — the rate is plan-specific, and without the
    partition prune this scans every network for the code (~8 s vs ~0.2 s)."""
    if not network_name:
        raise HTTPException(
            400,
            detail={"code": "network_required",
                    "message": "Pick your plan to see the rate at this provider."},
        )
    conn = db()

    params: list = [npi, billing_code, billing_code_type, network_slug(network_name)]

    rows = conn.execute(f"""
        WITH npi_groups AS (
            SELECT DISTINCT file_id, provider_group_id
            FROM {GROUP_MEMBERS_SRC} WHERE npi = ?
        ),
        npi_sets AS (
            -- the file_id predicate lets DuckDB prune group_sets' Parquet row
            -- groups (physically file_id-clustered, one source file per
            -- Anthem file_id) instead of scanning the full 1e9+-row table
            -- (#100 live-corpus finding: this join alone was ~2s without it —
            -- the consolidated group_sets.parquet has no partition key of
            -- its own, unlike `rates`, which is Hive-partitioned by net).
            SELECT DISTINCT gs.file_id, gs.group_set_id
            FROM {GROUP_SETS_SRC} gs
            JOIN npi_groups g
              ON g.file_id = gs.file_id AND g.provider_group_id = gs.provider_group_id
            WHERE gs.file_id IN (SELECT DISTINCT file_id FROM npi_groups)
        )
        SELECT p.modifier, p.service_code, p.setting, p.negotiated_type,
               MIN(p.negotiated_rate), MAX(p.negotiated_rate), COUNT(*)
        FROM {RATES_SRC} p
        JOIN npi_sets s ON s.file_id = p.file_id AND s.group_set_id = p.group_set_id
        WHERE p.billing_code = ? AND p.billing_code_type = ?
          AND NOT p.is_sentinel AND p.net = ?
          AND p.scope = 'outpatient_prof'
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

    # Medicare Physician Fee Schedule benchmark (issue #61) — the GA allowed
    # amount for this code + a headline/Medicare ratio. Both None until MPFS
    # was a build input; never fail the quote when it wasn't.
    med_allowed = _medicare_allowed(conn, billing_code, billing_code_type)
    vs_medicare = None
    if med_allowed and headline.get("rate"):
        vs_medicare = round(headline["rate"] / med_allowed, 2)

    card = provider_card(conn, npi)

    # Medicare Part B evidence (issue #14). `util` and `med_spec` are None until
    # CMS utilization was a build input.
    util = did_bill(conn, npi, billing_code)
    med_spec = medicare_specialty(conn, npi)

    plaus = None
    if card:
        lab = conn.execute(f"""
            SELECT category, rbcs_family FROM {CODE_DIM_SRC}
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
    tier = code_tiers(conn, npi, [billing_code]).get(billing_code, "group") if card else None

    strip_private(card)

    return {
        "billing_code": billing_code,
        "billing_code_type": billing_code_type,
        "npi": npi,
        "network_name": network_name,
        "tier": tier,
        "provider": card,
        "plausibility": plaus,
        "medicare_utilization": util,
        "medicare_allowed": med_allowed,
        "vs_medicare": vs_medicare,
        "headline": headline,
        "components": components,
        "is_component_split": split,
    }
