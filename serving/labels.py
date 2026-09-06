"""Consumer-facing labels — turning raw MRF codes into something a patient reads.

Place-of-service buckets, modifier labels, the provider card (one `provider_dim`
row + its hospital affiliations), and the coarse specialty↔code plausibility
check. No SQL sources here — see data_sources.py.
"""
import datetime

from .data_sources import PROVIDER_AFFIL_SRC, PROVIDER_DIM_SRC

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

# ── provider card ──────────────────────────────────────────────────────────

_BEHAVIORAL = ("social worker", "counselor", "psychologist", "behavior analyst",
               "psychiatric", "mental health", "behavioral health")
_PROCEDURAL_CATS = {"Procedure", "Imaging", "Test", "Anesthesia"}


def provider_card(conn, npi: int):
    """Name / specialty / practice address for one NPI from `provider_dim`, plus
    the CMS Doctors & Clinicians identity the build folded in (`group_name`,
    `years_in_practice`) and its `hospital_affiliations` (`[{ccn,
    facility_name}]`, from `provider_affiliations`). Keys prefixed `_` are for
    the caller (plausibility / evidence lookups) and are popped before the
    card is returned to a client. None when the NPI isn't in NPPES GA."""
    r = conn.execute(f"""
        SELECT name, city, postal_code, is_hospital, specialty, nucc_grouping,
               nucc_classification, address_line1, address_line2, is_clinic,
               entity_type, group_name, grad_year, cms_provider_type
        FROM {PROVIDER_DIM_SRC}
        WHERE npi = ?
        LIMIT 1
    """, [npi]).fetchone()
    if not r:
        return None
    street = ", ".join(x for x in (r[7], r[8]) if x)
    yr = r[12]
    this_year = datetime.date.today().year
    affils = conn.execute(f"""
        SELECT DISTINCT ccn, facility_name FROM {PROVIDER_AFFIL_SRC}
        WHERE npi = ? ORDER BY facility_name NULLS LAST, ccn
    """, [npi]).fetchall()
    return {
        "npi": npi, "name": r[0], "city": r[1], "postal_code": r[2],
        "is_hospital": bool(r[3]), "specialty": r[4],
        "is_clinic": bool(r[9]), "entity_type": r[10],
        "street": street or None,
        "address": ", ".join(x for x in (street, r[1]) if x) or None,
        "group_name": r[11],
        "years_in_practice": (this_year - yr if yr and 1900 < yr <= this_year else None),
        "hospital_affiliations": [{"ccn": a[0], "facility_name": a[1]} for a in affils],
        "_grouping": r[5], "_classification": r[6], "_cms_provider_type": r[13],
    }


def strip_private(card):
    """Drop the `_`-prefixed working keys before a card leaves the API."""
    if card:
        for k in [k for k in card if k.startswith("_")]:
            card.pop(k)
    return card


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
