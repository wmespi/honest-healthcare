"""Service-line taxonomy allowlists (issue #83).

A service line is a deliberate narrowing — "every NPPES provider" cut down to
"providers relevant to one specific consumer question" — the opposite move
from the rest of the app, which has mostly widened. Each entry below is a
curated list of NUCC taxonomy codes, verified against the real NUCC Health
Care Provider Taxonomy Code Set (`data/reference/nucc_taxonomy.parquet`) and
cross-checked against real GA NPPES + rate-corpus counts — not assumed.
"""

# PCP — "who should I pick as my primary care provider" (#83).
#
# Physicians: the GENERAL Family Medicine / Internal Medicine / General
# Practice codes only — i.e. `specialization IS NULL`. A plain
# `classification = 'Internal Medicine'` filter is a trap: NUCC nests every
# Internal Medicine subspecialty (Cardiovascular Disease, Gastroenterology,
# Nephrology, ...) under that same classification, distinguished only by
# `specialization`. Using the classification alone would pull cardiologists
# and gastroenterologists into a "PCP" list.
#
# Nurse Practitioners: included in v1 (not a fast-follow) — excluding them
# would skew a cost/quality "best value" ranking, since NPs are a large share
# of real-world primary care. NP taxonomy codes carry the primary-care signal
# directly in `specialization` (Family / Primary Care / Adult Health), unlike
# physicians.
#
# Verified 2026-09-04 against GA NPPES + the current rate corpus:
#   24,697 GA providers match this code set (of 229,443 total, unfiltered —
#   an 89% cut); 11,905 already have a priceable rate.
PCP_TAXONOMY_CODES = [
    "207Q00000X",  # Family Medicine (general)
    "207R00000X",  # Internal Medicine (general)
    "208D00000X",  # General Practice
    "363LF0000X",  # Nurse Practitioner — Family
    "363LP2300X",  # Nurse Practitioner — Primary Care
    "363LA2200X",  # Nurse Practitioner — Adult Health
    "363L00000X",  # Nurse Practitioner — generic (no specialization on file)
]

SERVICE_LINES = {
    "pcp": PCP_TAXONOMY_CODES,
}

# The billing-code family a service line actually shops for — distinct from
# the *taxonomy* allowlist above (which providers count) and used to cost-sort
# them (#87 follow-up). Mirrors frontend/src/App.jsx's SERVICE_LINE_CODES,
# which narrows the provider menu to the same family — kept in sync by hand,
# same as the rest of the frontend/backend split; there's no shared build step
# between the two stacks to source it from one place.
SERVICE_LINE_BILLING_CODES = {
    "pcp": ["99202", "99203", "99204", "99205",
            "99381", "99382", "99383", "99384", "99385", "99386", "99387"],
}
