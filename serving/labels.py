"""Consumer-facing labels — turning raw MRF codes into something a patient reads.

Place-of-service buckets, modifier labels, the NUCC specialty join, the provider
card, and the coarse specialty↔code plausibility check. No SQL sources here — see
data_sources.py.
"""
import os

from .data_sources import GA_NPPES_PATH, NUCC_PATH, nppes_cols

# ── place of service ────────────────────────────────────────────────────────

POS_LABELS = {
    "office": "Office / telehealth",
    "asc": "Ambulatory surgery center",
    "er": "Emergency room",
    "inpatient": "Hospital inpatient",
    "hosp_outpatient": "Hospital outpatient dept.",
    "any": "Any setting",
    "unspecified": "Setting not specified",
    "facility": "Facility setting",
}


def pos_bucket(service_code):
    """Collapse a `|`-joined CMS place-of-service list to one consumer bucket.
    A long list means one rate that applies across settings -> "any"."""
    codes = {c for c in (service_code or "").split("|") if c}
    if not codes:
        return "unspecified"
    if len(codes) >= 4:
        return "any"
    if "24" in codes:
        return "asc"
    if "23" in codes:
        return "er"
    if codes & {"21", "51", "61"}:
        return "inpatient"
    if codes & {"22", "19", "20"}:
        return "hosp_outpatient"
    if codes & {"11", "10", "12", "02", "72"}:
        return "office"
    return "facility"


MODIFIER_LABELS = {
    "": ("Full procedure", "The complete service — physician work plus facility/equipment."),
    "26": ("Professional fee", "The physician's work only — reading, interpretation, supervision."),
    "TC": ("Technical fee", "Facility, equipment, and staff only — no physician work."),
    "26|TC": ("Professional + technical", "Billed as separate components that sum to the global rate."),
    "QW": ("CLIA-waived test", "Simple lab test run in-office."),
    "53": ("Discontinued procedure", "Stopped before completion."),
    "50": ("Bilateral procedure", "Performed on both sides."),
}

# ── NUCC specialty + provider card ─────────────────────────────────────────

_BEHAVIORAL = ("social worker", "counselor", "psychologist", "behavior analyst",
               "psychiatric", "mental health", "behavioral health")
_PROCEDURAL_CATS = {"Procedure", "Imaging", "Test", "Anesthesia"}


def nucc_bits():
    """(select-fragment, join-fragment) adding `specialty` + `grouping` +
    `classification` columns off nx, or NULLs when the NUCC reference isn't built
    yet. Assumes the provider row exposes `taxonomy_code` as `g.taxonomy_code`."""
    if os.path.exists(NUCC_PATH):
        return (
            "COALESCE(nx.specialty, NULLIF(g.taxonomy_group, 'Other')) AS specialty, "
            "nx.grouping AS nucc_grouping, nx.classification AS nucc_classification",
            f"LEFT JOIN read_parquet('{NUCC_PATH}') nx ON nx.taxonomy_code = g.taxonomy_code",
        )
    return ("NULLIF(g.taxonomy_group, 'Other') AS specialty, NULL AS nucc_grouping, "
            "NULL AS nucc_classification", "")


def provider_card(conn, npi: int):
    """Name / specialty / practice address for one NPI from the NPPES GA subset."""
    if not os.path.exists(GA_NPPES_PATH):
        return None
    spec_sel, spec_join = nucc_bits()
    have_addr = {"address_line1", "address_line2"} <= nppes_cols(conn)
    addr_sel = "g.address_line1, g.address_line2" if have_addr else "NULL AS address_line1, NULL AS address_line2"
    r = conn.execute(f"""
        SELECT COALESCE(NULLIF(g.org_name, ''),
                        NULLIF(TRIM(BOTH ', ' FROM g.last_name || ', ' || g.first_name), '')) AS name,
               g.city, g.postal_code, g.is_hospital, {spec_sel}, {addr_sel},
               g.is_clinic, g.entity_type
        FROM read_parquet('{GA_NPPES_PATH}') g
        {spec_join}
        WHERE g.npi = ?
        LIMIT 1
    """, [npi]).fetchone()
    if not r:
        return None
    # cols: name0 city1 postal2 is_hospital3 specialty4 grouping5 classification6 addr1_7 addr2_8 is_clinic9 entity_type10
    street = ", ".join(x for x in (r[7], r[8]) if x)
    return {
        "npi": npi, "name": r[0], "city": r[1], "postal_code": r[2],
        "is_hospital": bool(r[3]), "specialty": r[4],
        "is_clinic": bool(r[9]), "entity_type": r[10],
        "_grouping": r[5], "_classification": r[6],
        "street": street or None,
        "address": ", ".join(x for x in (street, r[1]) if x) or None,
    }


def plausibility(grouping, classification, specialty, rbcs_category, rbcs_family,
                 medicare_type=None):
    """Coarse signal for "is this code within the provider's declared specialty?"
    NOT proof of what they do or don't bill — for that see the CMS utilization
    evidence layer (serving/evidence.py, GH #14), which overrides this. "unlikely"
    only means the code sits well outside the provider's specialty, so the
    frontend should present the number as the *group's* rate.

    `medicare_type` is CMS's own rendering-provider specialty label, available for
    the ~14% of providers with Part B claims. It's folded into the specialty text
    because it's usually cleaner than a stale / vague self-reported NUCC taxonomy
    ("Specialist"). Returns "unlikely" | "typical" | None."""
    who = " ".join(
        x for x in (grouping, classification, specialty, medicare_type) if x
    ).lower()
    fam = (rbcs_family or "").lower()
    is_behavioral = any(t in who for t in _BEHAVIORAL)
    is_psych_code = "psychotherapy" in fam or "psychiatr" in fam or "mental health" in fam
    if is_behavioral and rbcs_category in _PROCEDURAL_CATS and not is_psych_code:
        return "unlikely"
    if not is_behavioral and who and is_psych_code:
        return "unlikely"
    if not who or not rbcs_category:
        return None
    return "typical"
