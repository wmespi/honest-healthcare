"""Provider endpoints.

  /providers/{npi}/procedures  job 4 — the provider "menu"
  /providers/search            name / org / city / NPI / specialty search (NPPES GA)
  /specialties                 NUCC classifications we hold providers for
  /providers/ga                raw NPPES GA lookup
"""
import os
from typing import Optional

from fastapi import APIRouter, Query

from ..data_sources import (
    DAC_GA_PATH,
    GA_NPPES_PATH,
    CODE_LABELS_PATH,
    NPI_LOOKUP_PATH,
    NUCC_PATH,
    PRICES_SRC,
    PROVIDERS_GLOB,
    PROVIDERS_SRC,
    GROUP_SETS_SRC,
    db,
    has_parquet,
    network_slug,
)


def _rated_npi(network_name: str | None):
    """(cte, params, predicate) for "this NPI has a negotiated rate". Scoped to
    `network_name` when given (via the network-attributed `providers` roster —
    `npi_lookup` is corpus-wide and overstates coverage for a narrow network like
    Blue Value, GH #10 / known-gaps "specialty counts aren't plan-scoped").
    Assumes the outer query exposes the NPPES row as `g`."""
    if network_name and has_parquet(PROVIDERS_GLOB):
        return (
            f"rated_npi AS (SELECT DISTINCT npi FROM {PROVIDERS_SRC} WHERE network_name = ?)",
            [network_name],
            "g.npi IN (SELECT npi FROM rated_npi)",
        )
    if os.path.exists(NPI_LOOKUP_PATH):
        return (
            f"rated_npi AS (SELECT npi FROM read_parquet('{NPI_LOOKUP_PATH}', union_by_name=true))",
            [],
            "g.npi IN (SELECT npi FROM rated_npi)",
        )
    return (None, [], "FALSE")
from ..labels import dac_bits, nucc_bits, provider_card
from ..evidence import (
    DEFAULT_TYPICAL_THRESHOLD,
    all_billed_codes,
    billed_codes,
    typical_codes,
)

router = APIRouter()


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
                            that ≥ `typical_threshold` of their specialty bills
                            ("typical"); `group_count` reports how many were hidden
      all                 — every contracted code, each tagged with its tier
    Falls back to `all` (with `group_rate_only: true`) when a provider has no
    plausible codes, or the CMS reference files aren't built.

    Resolves the NPI to its (file_id, group_set_id) sets FIRST (cheap), then
    touches `prices`.
    """
    conn = db()

    net_filter = "AND p.net = ?" if network_name else ""
    set_filter = "AND p.setting = ?" if setting else ""
    params: list = [npi]
    if network_name:
        params.append(network_slug(network_name))
    if setting:
        params.append(setting)

    # Provider specialty (NUCC classification) — the key into the Tier-2 profile.
    pcard = provider_card(conn, npi)
    specialty = (pcard or {}).get("_classification")
    if pcard:
        pcard.pop("_grouping", None)
        pcard.pop("_classification", None)

    billed_set = all_billed_codes(conn, npi)
    typical_set = typical_codes(conn, specialty, typical_threshold)
    plausible_set = billed_set | typical_set
    # `all` when asked, when nothing to filter on, or when a search is active
    # (the user is looking for something specific — don't hide it).
    effective_tier = "all" if (tier == "all" or q or not plausible_set) else "plausible"

    has_labels = os.path.exists(CODE_LABELS_PATH)
    if has_labels:
        label_join = f"LEFT JOIN read_parquet('{CODE_LABELS_PATH}') l ON l.billing_code = m.billing_code AND l.billing_code_type = m.billing_code_type"
        label_cols = "l.label, l.rbcs_category, l.rbcs_subcategory"
    else:
        label_join, label_cols = "", "NULL AS label, NULL AS rbcs_category, NULL AS rbcs_subcategory"

    def fetch_menu(eff_tier):
        wheres, extra_params = [], []
        if q:
            if has_labels:
                wheres.append("(m.billing_code ILIKE ? OR l.search_text ILIKE ?)")
                extra_params += [f"%{q}%", f"%{q}%"]
            else:
                wheres.append("m.billing_code ILIKE ?")
                extra_params.append(f"%{q}%")
        if eff_tier == "plausible":
            placeholders = ", ".join("?" * len(plausible_set))
            wheres.append(f"m.billing_code IN ({placeholders})")
            extra_params += sorted(plausible_set)
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        return conn.execute(f"""
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
            {where_sql}
            ORDER BY m.n_rates DESC, m.billing_code
            LIMIT {limit}
        """, params + extra_params).fetchall()

    rows = fetch_menu(effective_tier)

    # total distinct menu codes (pre-tier-filter, post-search) → how many the
    # plausible view is hiding.
    total_menu = conn.execute(f"""
        WITH npi_groups AS (
            SELECT DISTINCT file_id, provider_group_id FROM {PROVIDERS_SRC} WHERE npi = ?
        ),
        npi_sets AS (
            SELECT DISTINCT gs.file_id, gs.group_set_id FROM {GROUP_SETS_SRC} gs
            JOIN npi_groups g ON g.file_id = gs.file_id AND g.provider_group_id = gs.provider_group_id
        )
        SELECT COUNT(DISTINCT p.billing_code)
        FROM {PRICES_SRC} p
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
    network_name: Optional[str] = None,
    limit: int = Query(default=20, le=100),
):
    """Search providers by name, organization, city, or NPI against the NPPES
    Georgia subset. Providers we actually hold rate data for are returned first
    (`has_rates`). A purely numeric query is treated as an NPI prefix. `specialty`
    filters on the NUCC label (e.g. "cardio", "orthopa"). `network_name` scopes
    `has_rates` to that plan — without it, `has_rates` means "priced in some
    Anthem network", which overstates a narrow network."""
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

    rated_cte, rated_params, has_rates_expr = _rated_npi(network_name)

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
        # Match the *displayed* specialty label only — NOT the NUCC `classification`
        # / `grouping`, which lump distinct specialities together ("Psychiatry &
        # Neurology" would return every neurologist for a "Psychiatry" pick, and
        # the grouping is broader still). Same COALESCE the SELECT uses so the
        # filter and the label agree.
        conds.append(
            "COALESCE(nx.specialty, NULLIF(g.taxonomy_group, 'Other'), '') ILIKE ?"
            if spec_join else "g.taxonomy_group ILIKE ?"
        )
        params.append(f"%{specialty}%")
    where = " AND ".join(conds) if conds else "1=1"

    # Annotate each row with the CMS Doctors & Clinicians group name — the real
    # practice identity, independent of Anthem's buckets. Light touch: a guarded
    # LEFT JOIN, NULL when `make doctors-clinicians` hasn't run.
    has_dac, _ = dac_bits()
    if has_dac:
        dac_join = f"LEFT JOIN read_parquet('{DAC_GA_PATH}') d ON d.npi = g.npi"
        dac_sel = "d.org_name AS group_name"
    else:
        dac_join, dac_sel = "", "NULL AS group_name"

    with_sql = f"WITH {rated_cte}" if rated_cte else ""
    rows = conn.execute(f"""
        {with_sql}
        SELECT g.npi,
               COALESCE(NULLIF(g.org_name, ''),
                        NULLIF(TRIM(BOTH ', ' FROM g.last_name || ', ' || g.first_name), ''),
                        CAST(g.npi AS VARCHAR)) AS name,
               g.city, g.taxonomy_group, g.is_hospital, g.is_clinic,
               {has_rates_expr} AS has_rates,
               g.entity_type,
               {dac_sel},
               {spec_sel}
        FROM read_parquet('{GA_NPPES_PATH}') g
        {spec_join}
        {dac_join}
        WHERE {where}
        -- individuals carry the rates; a specialty search wants doctors, not
        -- the practice's org NPI (which is never in a roster).
        ORDER BY has_rates DESC, (g.entity_type = 'individual') DESC,
                 g.is_hospital DESC, g.is_clinic DESC, name
        LIMIT {limit}
    """, rated_params + params).fetchall()
    cols = ["npi", "name", "city", "taxonomy_group", "is_hospital", "is_clinic",
            "has_rates", "entity_type", "group_name", "specialty"]
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
    the "has any rated provider" filter) to that plan. Cheap: a scan of the NPPES
    GA subset joined to the small NUCC table."""
    if not os.path.exists(GA_NPPES_PATH) or not os.path.exists(NUCC_PATH):
        return []
    conn = db()
    rated_cte, rated_params, rated = _rated_npi(network_name)
    with_sql = f"WITH {rated_cte}" if rated_cte else ""
    # x.specialty = specialization else classification — the clean label a
    # patient recognises ("Cardiovascular Disease", not the "Internal Medicine"
    # classification that NUCC actually files cardiologists under).
    where = "WHERE COALESCE(x.specialty, '') <> ''"
    params: list = []
    if q.strip():
        where += " AND x.specialty ILIKE ?"
        params.append(f"%{q.strip()}%")
    rows = conn.execute(f"""
        {with_sql}
        SELECT x.specialty AS specialty,
               COUNT(DISTINCT g.npi) AS n_providers,
               COUNT(DISTINCT CASE WHEN {rated} THEN g.npi END) AS n_with_rates
        FROM read_parquet('{GA_NPPES_PATH}') g
        JOIN read_parquet('{NUCC_PATH}') x ON x.taxonomy_code = g.taxonomy_code
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
