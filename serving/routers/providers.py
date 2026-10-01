"""Provider endpoints.

  /providers/{npi}/procedures  job 4 — the provider "menu"
  /providers/search            name / org / city / NPI / specialty search
  /specialties                 NUCC classifications we hold providers for
  /providers/ga                raw provider_dim lookup
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..data_sources import (
    CODE_DIM_SRC,
    EVIDENCE_SRC,
    GROUP_MEMBERS_SRC,
    GROUP_NETWORKS_SRC,
    GROUP_SETS_SRC,
    PROVIDER_DIM_SRC,
    RATES_SRC,
    ZIP_CENTROIDS_SRC,
    db,
    network_slug,
)
from ..evidence import DEFAULT_TYPICAL_THRESHOLD, all_billed_codes, billed_codes, typical_codes
from ..labels import provider_card, strip_private
from ..ranking import haversine_sql, pct_good_sql, score_sql
from ..service_lines import SERVICE_LINES, SERVICE_LINE_BILLING_CODES

router = APIRouter()


def _rated_npi(network_name: str | None):
    """(cte, params, predicate) for "this NPI has a negotiated rate". Scoped to
    `network_name` when given, via `group_networks` — the file-local group's
    network attribution — joined through `group_members` (GH #10 / known-gaps
    "specialty counts aren't plan-scoped"). Assumes the outer query exposes the
    provider_dim row as `g`."""
    if network_name:
        return (
            f"""rated_npi AS (
                SELECT DISTINCT gm.npi
                FROM {GROUP_MEMBERS_SRC} gm
                JOIN {GROUP_NETWORKS_SRC} gn
                  ON gn.file_id = gm.file_id AND gn.provider_group_id = gm.provider_group_id
                WHERE gn.network_name = ?
            )""",
            [network_name],
            "g.npi IN (SELECT npi FROM rated_npi)",
        )
    return (
        f"rated_npi AS (SELECT DISTINCT npi FROM {GROUP_MEMBERS_SRC})",
        [],
        "g.npi IN (SELECT npi FROM rated_npi)",
    )


def _service_line_rate_cte(service_line: str, network_name: str | None, codes: list[str]):
    """(cte, params) for "the cheapest *plausible* in-scope rate this NPI has,
    in this network" — the ranking signal a service-line list (#83, #87)
    needs and a plain specialty search doesn't: `service_line` names an exact
    code family (e.g. PCP_TAXONOMY_CODES' new-patient-visit codes), so unlike
    `has_rates` (any rate at all, from `_rated_npi`) this is specific to the
    codes the consumer actually came here for. Needs a network — a rate is
    plan-specific — so returns (None, []) without one; the caller falls back
    to the existing unranked order.

    A naive MIN() over every price this NPI's provider group can reach picks
    up Anthem's well-known billing-group fan-out (issue #14, #73): one code's
    rock-bottom rate can sit in a group_set shared by thousands of NPIs
    statewide, so almost everyone ties on the network's floor and the ranking
    stops meaning anything (caught live testing #87 — every PCP showed the
    same $38). So this prefers the same "plausible" tier
    `/providers/{npi}/procedures` already uses — a code this NPI actually
    billed to Medicare, or one typical for their NUCC classification, read
    straight off the build's `evidence` table — and only falls back to the raw
    group-wide floor for an NPI with no plausible-tier rate at all (same
    fallback the menu endpoint uses).

    Same npi_groups -> group_sets -> rates path as the menu query, scoped by
    the `net` Hive partition key (network_name's slug) so DuckDB prunes to one
    partition before the code filter runs — cheap even against the full
    corpus."""
    if not (service_line and network_name and codes):
        return None, []
    placeholders = ", ".join("?" * len(codes))
    cte = f"""
        -- Scope to this network's groups FIRST, via group_networks — group_sets
        -- is a flat, unpartitioned 1e9+-row roster table (every file, every
        -- network); joining it before narrowing by network is what OOM'd here
        -- (#100 regression found in live-corpus verification). The file_id
        -- predicate on sl_sets lets DuckDB prune group_sets' Parquet row groups
        -- (physically file_id-clustered, one source file per Anthem file_id),
        -- not just filter after a full scan.
        sl_net_groups AS (
            SELECT DISTINCT file_id, provider_group_id
            FROM {GROUP_NETWORKS_SRC}
            WHERE network_name = ?
        ),
        sl_groups AS (
            SELECT DISTINCT gm.npi, gm.file_id, gm.provider_group_id
            FROM {GROUP_MEMBERS_SRC} gm
            JOIN sl_net_groups ng ON ng.file_id = gm.file_id AND ng.provider_group_id = gm.provider_group_id
        ),
        sl_sets AS (
            SELECT DISTINCT sg.npi, gs.file_id, gs.group_set_id
            FROM {GROUP_SETS_SRC} gs
            JOIN sl_groups sg ON sg.file_id = gs.file_id AND sg.provider_group_id = gs.provider_group_id
            WHERE gs.file_id IN (SELECT DISTINCT file_id FROM sl_net_groups)
        ),
        sl_prices AS (
            SELECT ss.npi, p.billing_code, p.negotiated_rate
            FROM {RATES_SRC} p
            JOIN sl_sets ss ON ss.file_id = p.file_id AND ss.group_set_id = p.group_set_id
            WHERE p.net = ? AND p.billing_code IN ({placeholders})
              AND p.scope = 'outpatient_prof' AND NOT p.is_sentinel
        ),
        sl_scored AS (
            SELECT sp.npi, sp.negotiated_rate, (ev.billing_code IS NOT NULL) AS is_plausible
            FROM sl_prices sp
            LEFT JOIN (SELECT DISTINCT npi, billing_code FROM {EVIDENCE_SRC}) ev
              ON ev.npi = sp.npi AND ev.billing_code = sp.billing_code
        ),
        sl_rate AS (
            SELECT npi,
                   COALESCE(MIN(negotiated_rate) FILTER (WHERE is_plausible),
                            MIN(negotiated_rate)) AS min_rate,
                   BOOL_OR(is_plausible) AS min_rate_is_plausible
            FROM sl_scored
            GROUP BY 1
        )
    """
    params = [network_name, network_slug(network_name)] + codes
    return cte, params


@router.get("/service_lines")
def get_service_lines():
    """The curated service-line taxonomy + billing-code allowlists
    (`serving/service_lines.py`, issue #83) — so the frontend can read them
    from the API instead of keeping its own hand-synced copy (Step 5,
    frontend/src/App.jsx's SERVICE_LINE_CODES)."""
    return {
        name: {"taxonomy_codes": codes,
               "billing_codes": SERVICE_LINE_BILLING_CODES.get(name, [])}
        for name, codes in SERVICE_LINES.items()
    }


@router.get("/providers/{npi}/procedures")
def provider_procedures(
    npi: int,
    network_name: Optional[str] = None,
    setting: Optional[str] = None,
    q: str = Query(default=""),
    tier: str = Query(default="plausible", pattern="^(plausible|all)$"),
    typical_threshold: float = Query(default=DEFAULT_TYPICAL_THRESHOLD, ge=0, le=1),
    limit: int = Query(default=500, le=2000),
):
    """The provider "menu": procedures this NPI has a negotiated rate for.

    Anthem's provider groups are coarse — a provider "has" a rate for every code
    contracted to any group they sit in (issue #14). `tier` controls the noise:
      plausible (default) — only codes this NPI billed to Medicare ("billed") or
                            that are typical for their specialty ("typical"),
                            read off the build's `evidence` table; `group_count`
                            reports how many were hidden
      all                 — every contracted code, each tagged with its tier
    Falls back to `all` (with `group_rate_only: true`) when a provider has no
    plausible codes.

    Resolves the NPI to its (file_id, group_set_id) sets FIRST (cheap), then
    touches `rates`.
    """
    conn = db()

    net_filter = "AND p.net = ?" if network_name else ""
    set_filter = "AND p.setting = ?" if setting else ""
    params: list = [npi]
    if network_name:
        params.append(network_slug(network_name))
    if setting:
        params.append(setting)

    pcard = provider_card(conn, npi)
    specialty = (pcard or {}).get("specialty")

    billed_set = all_billed_codes(conn, npi)
    typical_set = typical_codes(conn, npi, typical_threshold)
    plausible_set = billed_set | typical_set
    # `all` when asked, when nothing to filter on, or when a search is active
    # (the user is looking for something specific — don't hide it).
    effective_tier = "all" if (tier == "all" or q or not plausible_set) else "plausible"

    def fetch_menu(eff_tier):
        wheres, extra_params = [], []
        if q:
            wheres.append("(m.billing_code ILIKE ? OR cd.search_text ILIKE ?)")
            extra_params += [f"%{q}%", f"%{q}%"]
        if eff_tier == "plausible":
            placeholders = ", ".join("?" * len(plausible_set))
            wheres.append(f"m.billing_code IN ({placeholders})")
            extra_params += sorted(plausible_set)
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        return conn.execute(f"""
            WITH npi_groups AS (
                SELECT DISTINCT file_id, provider_group_id
                FROM {GROUP_MEMBERS_SRC}
                WHERE npi = ?
            ),
            npi_sets AS (
                -- file_id predicate: lets DuckDB prune group_sets' Parquet row
                -- groups instead of scanning the full 1e9+-row table (#100).
                SELECT DISTINCT gs.file_id, gs.group_set_id
                FROM {GROUP_SETS_SRC} gs
                JOIN npi_groups g
                  ON g.file_id = gs.file_id AND g.provider_group_id = gs.provider_group_id
                WHERE gs.file_id IN (SELECT DISTINCT file_id FROM npi_groups)
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
                FROM {RATES_SRC} p
                JOIN npi_sets s ON s.file_id = p.file_id AND s.group_set_id = p.group_set_id
                WHERE 1=1 {net_filter} {set_filter}
                GROUP BY 1, 2
            )
            SELECT m.billing_code, m.billing_code_type,
                   ROUND(COALESCE(m.g_min, m.a_min), 2),
                   ROUND(COALESCE(m.g_med, m.a_med), 2),
                   ROUND(COALESCE(m.g_max, m.a_max), 2),
                   m.n_rates, m.n_networks, m.is_split, (m.g_n > 0) AS has_global,
                   cd.label, cd.category, cd.rbcs_subcategory
            FROM menu m
            LEFT JOIN {CODE_DIM_SRC} cd
              ON cd.billing_code = m.billing_code AND cd.billing_code_type = m.billing_code_type
            {where_sql}
            ORDER BY m.n_rates DESC, m.billing_code
            LIMIT {limit}
        """, params + extra_params).fetchall()

    rows = fetch_menu(effective_tier)

    # total distinct menu codes (pre-tier-filter, post-search) → how many the
    # plausible view is hiding.
    total_menu = conn.execute(f"""
        WITH npi_groups AS (
            SELECT DISTINCT file_id, provider_group_id FROM {GROUP_MEMBERS_SRC} WHERE npi = ?
        ),
        npi_sets AS (
            SELECT DISTINCT gs.file_id, gs.group_set_id FROM {GROUP_SETS_SRC} gs
            JOIN npi_groups g ON g.file_id = gs.file_id AND g.provider_group_id = gs.provider_group_id
            WHERE gs.file_id IN (SELECT DISTINCT file_id FROM npi_groups)
        )
        SELECT COUNT(DISTINCT p.billing_code)
        FROM {RATES_SRC} p
        JOIN npi_sets s ON s.file_id = p.file_id AND s.group_set_id = p.group_set_id
        WHERE 1=1 {net_filter} {set_filter}
    """, [npi] + params[1:]).fetchone()[0]

    # Plausible filtered everything out but the provider *does* have contracted
    # rates (none billed/typical for the specialty) — fall back to the full menu
    # rather than a dead "no rates" screen.
    if effective_tier == "plausible" and not rows and total_menu > 0:
        effective_tier = "all"
        rows = fetch_menu("all")

    billed = billed_codes(conn, npi, [r[0] for r in rows])

    def row_tier(code):
        if code in billed_set:
            return "billed"
        if code in typical_set:
            return "typical"
        return "group"

    results = [
        {
            "billing_code": r[0], "billing_code_type": r[1],
            "min_rate": r[2], "median_rate": r[3], "max_rate": r[4],
            "n_rates": r[5], "n_networks": r[6],
            "is_split": bool(r[7]), "has_global": bool(r[8]),
            "label": r[9], "rbcs_category": r[10], "rbcs_subcategory": r[11],
            "medicare": billed.get(r[0]),
            "tier": row_tier(r[0]),
        }
        for r in rows
    ]
    strip_private(pcard)
    return {
        "npi": npi,
        "provider": pcard,
        "specialty": specialty,
        "tier": effective_tier,
        "count": len(results),
        # how many contracted codes are hidden / would be Tier-3 noise
        "group_count": (
            max(total_menu - len(results), 0) if effective_tier == "plausible"
            else sum(1 for x in results if x["tier"] == "group")
        ),
        # provider has contracted rates but none is billed / typical for their
        # specialty — every row reaches them only via a shared billing group
        "group_rate_only": (
            effective_tier == "all" and not q and total_menu > 0
            and not any(x["tier"] in ("billed", "typical") for x in results)
        ),
        "results": results,
    }


@router.get("/providers/search")
def search_providers(
    q: str = Query(default=""),
    specialty: str = Query(default=""),
    service_line: str = Query(default=""),
    network_name: Optional[str] = None,
    zip_code: Optional[str] = Query(default=None, alias="zip", pattern=r"^\d{5}$"),
    radius_mi: Optional[float] = Query(default=None, gt=0, le=500),
    limit: int = Query(default=20, le=100),
):
    """Search providers by name, organization, city, or NPI against
    `provider_dim`. Providers we actually hold rate data for are returned
    first (`has_rates`). A purely numeric query is treated as an NPI prefix.
    `specialty` filters on the NUCC label (e.g. "cardio", "orthopa") — a fuzzy
    text match. `service_line` filters on an exact curated taxonomy-code
    allowlist instead (see `serving/service_lines.py`, e.g.
    `service_line=pcp`) — use this when the taxonomy codes that answer a
    question are known precisely and a fuzzy label would over- or under-match
    (`classification=Internal Medicine` alone pulls in every subspecialist;
    `specialty` text can't rule that out). `network_name` scopes `has_rates`
    to that plan — without it, `has_rates` means "priced in some Anthem
    network", which overstates a narrow network. When both `service_line` and
    `network_name` are given, each row also carries `min_rate` — the cheapest
    in-scope-code rate this NPI has on that plan — and the list is ranked on
    it (cheapest first, no-rate last) instead of just alphabetically; without
    a plan a rate can't be computed (it's plan-specific), so `min_rate` is
    null and the order is unchanged (#87).

    `zip` adds `distance_mi` (straight-line miles from the ZIP's centroid to the
    provider's geocoded address); `radius_mi` (needs `zip`) drops anyone farther
    or without coordinates. With a `service_line` and a `zip` the list is
    ordered on one blended `rank_score` (cost drops out without a plan); with a
    plan alone it stays cheapest-first. Weights and formula in `serving/ranking.py`. `mips_score` (CMS MIPS, where the clinician has one)
    and `years_in_practice` (DAC graduation year) ride on every row; they are
    different signals and are never substituted for each other."""
    q = q.strip()
    specialty = specialty.strip()
    service_line = service_line.strip().lower()
    if service_line and service_line not in SERVICE_LINES:
        raise HTTPException(400, f"unknown service_line: {service_line!r} "
                                  f"(known: {', '.join(sorted(SERVICE_LINES))})")
    if not q and not specialty and not service_line:
        return []
    if radius_mi is not None and not zip_code:
        raise HTTPException(400, "radius_mi needs zip")
    conn = db()
    here = None
    if zip_code:
        here = conn.execute(f"SELECT lat, lon FROM {ZIP_CENTROIDS_SRC} WHERE zip = ?", [zip_code]).fetchone()
        if not here:
            raise HTTPException(400, f"unknown zip: {zip_code}")

    rated_cte, rated_params, has_rates_expr = _rated_npi(network_name)

    conds, params = [], []
    if q.isdigit():
        conds.append("CAST(g.npi AS VARCHAR) LIKE ?")
        params.append(f"{q}%")
    elif q:
        conds.append("(g.org_name ILIKE ? OR g.name ILIKE ? OR g.city ILIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if specialty:
        # Match the *displayed* specialty label only — NOT the NUCC
        # `nucc_classification` / `nucc_grouping`, which lump distinct
        # specialities together ("Psychiatry & Neurology" would return every
        # neurologist for a "Psychiatry" pick, and the grouping is broader
        # still).
        conds.append("COALESCE(g.specialty, '') ILIKE ?")
        params.append(f"%{specialty}%")
    if service_line:
        # provider_dim.service_lines is the build's comma-joined tag list
        # (build/build.py's _service_line_expr) — an exact taxonomy-code
        # allowlist baked in at build time, the opposite move from `specialty`'s
        # fuzzy text match.
        conds.append("CONTAINS(',' || COALESCE(g.service_lines, '') || ',', ?)")
        params.append(f",{service_line},")
    where = " AND ".join(conds) if conds else "1=1"

    # Cost-sort (#87 follow-up): when a service line and a plan are both known,
    # the question isn't just "who's in network" but "who's cheapest for the
    # thing I came here for" — rank on it, not just on name.
    sl_cte, sl_params = _service_line_rate_cte(
        service_line, network_name, SERVICE_LINE_BILLING_CODES.get(service_line, []))
    sl_join = "LEFT JOIN sl_rate sr ON sr.npi = g.npi" if sl_cte else ""
    sl_sel = "ROUND(sr.min_rate, 2)" if sl_cte else "NULL"
    # whether min_rate came from a code this NPI actually billed / is typical
    # for their specialty, vs. the raw group-fanout floor (fallback when
    # there's no plausible-tier rate at all) — surfaced so the frontend never
    # shows a bare price that's secretly just the network's floor.
    sl_plausible_sel = "sr.min_rate_is_plausible" if sl_cte else "NULL"
    order_by_rate = "(min_rate IS NOT NULL) DESC, min_rate ASC, "

    if here:  # lat/lon came from our own zip_centroids table, so inlining them is safe
        dist_sel = (f"ROUND(CASE WHEN g.lat IS NULL THEN NULL ELSE "
                    f"{haversine_sql('g.lat', 'g.lon', repr(float(here[0])), repr(float(here[1])))} END, 1)")
    else:
        dist_sel = "NULL"
    radius_sql = "WHERE distance_mi <= ?" if radius_mi is not None else ""
    radius_params = [radius_mi] if radius_mi is not None else []

    blended = bool(service_line and here)
    ctes = [c for c in (rated_cte, sl_cte) if c]
    ctes.append(f"""cand AS (
        SELECT g.npi,
               COALESCE(NULLIF(g.org_name, ''), NULLIF(g.name, ''), CAST(g.npi AS VARCHAR)) AS name,
               g.city, g.nucc_classification AS taxonomy_group, g.is_hospital, g.is_clinic,
               {has_rates_expr} AS has_rates,
               g.entity_type, g.group_name,
               {sl_sel} AS min_rate,
               {sl_plausible_sel} AS min_rate_is_plausible,
               g.specialty,
               {dist_sel} AS distance_mi,
               g.mips_score, g.mips_year,
               CASE WHEN g.grad_year BETWEEN 1900 AND date_part('year', current_date)
                    THEN CAST(date_part('year', current_date) - g.grad_year AS INTEGER) END AS years_in_practice
        FROM {PROVIDER_DIM_SRC} g
        {sl_join}
        WHERE {where}
    )""")
    ctes.append(f"""scoped AS (SELECT * FROM cand {radius_sql})""")
    if blended:
        ctes.append(f"""scored AS (
            SELECT *, {pct_good_sql("min_rate")} AS cost_good,
                      {pct_good_sql("distance_mi")} AS distance_good,
                      {pct_good_sql("mips_score", best_high=True)} AS quality_good
            FROM scoped
        )""")
        final = f"""SELECT *, ROUND({score_sql(bool(sl_cte), bool(here))}, 3) AS rank_score FROM scored"""
        # a provider with no price on the plan, or no coordinates for the ZIP, can't be
        # compared on that axis — they follow everyone who can.
        order = ("(min_rate IS NOT NULL) DESC, " if sl_cte else "") + \
                ("(distance_mi IS NOT NULL) DESC, " if here else "") + \
                "rank_score DESC, has_rates DESC, name"
    else:
        final = "SELECT *, NULL AS rank_score FROM scoped"
        order = ((order_by_rate if sl_cte else "") +
                 ("distance_mi ASC NULLS LAST, " if here else "") +
                 "has_rates DESC, (entity_type = 'individual') DESC, is_hospital DESC, is_clinic DESC, name")
    rows = conn.execute(f"""
        WITH {', '.join(ctes)}
        SELECT npi, name, city, taxonomy_group, is_hospital, is_clinic, has_rates,
               entity_type, group_name, min_rate, min_rate_is_plausible, specialty,
               distance_mi, mips_score, mips_year, years_in_practice, rank_score
        FROM ({final})
        ORDER BY {order}
        LIMIT {limit}
    """, rated_params + sl_params + params + radius_params).fetchall()
    cols = ["npi", "name", "city", "taxonomy_group", "is_hospital", "is_clinic",
            "has_rates", "entity_type", "group_name", "min_rate",
            "min_rate_is_plausible", "specialty", "distance_mi", "mips_score",
            "mips_year", "years_in_practice", "rank_score"]
    return [dict(zip(cols, r)) for r in rows]


@router.get("/specialties")
def specialties(
    q: str = Query(default=""),
    network_name: Optional[str] = None,
    limit: int = Query(default=60, le=500),
):
    """NUCC classifications we hold GA providers for, with a provider count — the
    "pick your care" step of the plan-first flow (and the typeahead behind the
    "by specialty" search mode). Listed **alphabetically**; `n_with_rates` is
    shown, not ranked on — a patient scans for their specialty by name, and the
    count is context, not a sort key. `network_name` scopes `n_with_rates` (and
    the "has any rated provider" filter) to that plan. Cheap: a scan of
    `provider_dim`."""
    conn = db()
    rated_cte, rated_params, rated = _rated_npi(network_name)
    where = "WHERE COALESCE(g.specialty, '') <> ''"
    params: list = []
    if q.strip():
        where += " AND g.specialty ILIKE ?"
        params.append(f"%{q.strip()}%")
    rows = conn.execute(f"""
        WITH {rated_cte}
        SELECT g.specialty AS specialty,
               COUNT(DISTINCT g.npi) AS n_providers,
               COUNT(DISTINCT CASE WHEN {rated} THEN g.npi END) AS n_with_rates
        FROM {PROVIDER_DIM_SRC} g
        {where}
        GROUP BY 1
        HAVING COUNT(DISTINCT CASE WHEN {rated} THEN g.npi END) > 0
        ORDER BY specialty
        LIMIT {limit}
    """, rated_params + params).fetchall()
    return [{"specialty": r[0], "n_providers": r[1], "n_with_rates": r[2]} for r in rows]


@router.get("/providers/ga")
def ga_providers(
    q: str = Query(default=""),
    hospitals_only: bool = False,
    clinics_only: bool = False,
    limit: int = Query(default=50, le=500),
):
    """Search `provider_dim` (org name / city / NPI prefix)."""
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
        SELECT npi, entity_type, org_name, name, specialty, taxonomy_code,
               nucc_classification, is_hospital, is_clinic, city, postal_code
        FROM {PROVIDER_DIM_SRC}
        WHERE {" AND ".join(conds)}
        ORDER BY is_hospital DESC, is_clinic DESC, org_name
        LIMIT {limit}
    """, params).fetchall()
    cols = ["npi", "entity_type", "org_name", "name", "specialty", "taxonomy_code",
            "taxonomy_group", "is_hospital", "is_clinic", "city", "postal_code"]
    return {"available": True, "results": [dict(zip(cols, r)) for r in rows]}
