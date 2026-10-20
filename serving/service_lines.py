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

# Imaging — "where do I get this scan, and what does it cost" (#120). Anthem
# attaches a price to every provider in a billing group's roster, so a code's
# price list is full of practices (family medicine, GI) that only share a
# contract with a radiology group. The allowlist keeps the ones that actually
# read or perform imaging: diagnostic radiology and its sub-specialties, plus
# the radiology / MRI / mammography clinic-centers.
IMAGING_TAXONOMY_CODES = [
    "2085R0202X",  # Radiology — Diagnostic Radiology
    "2085N0700X",  # Radiology — Neuroradiology
    "2085B0100X",  # Radiology — Body Imaging
    "2085P0229X",  # Radiology — Pediatric Radiology
    "2085D0003X",  # Radiology — Diagnostic Neuroimaging
    "2085N0904X",  # Radiology — Nuclear Radiology
    "2085R0204X",  # Radiology — Vascular & Interventional Radiology
    "2085U0001X",  # Radiology — Diagnostic Ultrasound
    "261QM1200X",  # Clinic/Center — Magnetic Resonance Imaging (MRI)
    "261QR0200X",  # Clinic/Center — Radiology
    "261QR0206X",  # Clinic/Center — Radiology, Mammography
    "261QR0207X",  # Clinic/Center — Radiology, Mobile Mammography
    "261QR0208X",  # Clinic/Center — Radiology, Mobile
]

SERVICE_LINES = {
    "pcp": PCP_TAXONOMY_CODES,
    "imaging": IMAGING_TAXONOMY_CODES,
}

# The scans a patient actually shops for: (CPT, plain-language label, group).
# A short list on purpose — common outpatient imaging, one row per question a
# patient asks ("how much is a knee MRI"), not the whole CPT radiology chapter.
IMAGING_MENU = [
    ("71046", "Chest X-ray", "X-ray"),
    ("73030", "Shoulder X-ray", "X-ray"),
    ("73560", "Knee X-ray", "X-ray"),
    ("73610", "Ankle X-ray", "X-ray"),
    ("72100", "Lower-back X-ray", "X-ray"),
    ("77067", "Screening mammogram", "Breast and bone"),
    ("77066", "Diagnostic mammogram, both breasts", "Breast and bone"),
    ("77065", "Diagnostic mammogram, one breast", "Breast and bone"),
    ("77080", "Bone density scan (DEXA)", "Breast and bone"),
    ("76700", "Abdominal ultrasound", "Ultrasound"),
    ("76856", "Pelvic ultrasound", "Ultrasound"),
    ("76830", "Transvaginal ultrasound", "Ultrasound"),
    ("76805", "Pregnancy ultrasound", "Ultrasound"),
    ("76536", "Thyroid ultrasound", "Ultrasound"),
    ("93970", "Leg vein ultrasound", "Ultrasound"),
    ("70450", "Head CT", "CT"),
    ("71250", "Chest CT", "CT"),
    ("71260", "Chest CT with contrast", "CT"),
    ("74176", "Abdomen and pelvis CT", "CT"),
    ("74177", "Abdomen and pelvis CT with contrast", "CT"),
    ("72131", "Lower-back CT", "CT"),
    ("70551", "Brain MRI", "MRI"),
    ("70553", "Brain MRI with and without contrast", "MRI"),
    ("72148", "Lower-back MRI", "MRI"),
    ("72141", "Neck MRI", "MRI"),
    ("73721", "Knee MRI", "MRI"),
    ("73221", "Shoulder MRI", "MRI"),
]

SERVICE_LINE_MENUS = {"imaging": IMAGING_MENU}

# The billing-code family a service line actually shops for — distinct from
# the *taxonomy* allowlist above (which providers count) and used to cost-sort
# them (#87 follow-up). Mirrors frontend/src/App.jsx's SERVICE_LINE_CODES,
# which narrows the provider menu to the same family — kept in sync by hand,
# same as the rest of the frontend/backend split; there's no shared build step
# between the two stacks to source it from one place.
SERVICE_LINE_BILLING_CODES = {
    "pcp": ["99202", "99203", "99204", "99205",
            "99381", "99382", "99383", "99384", "99385", "99386", "99387"],
    "imaging": [code for code, _, _ in IMAGING_MENU],
}
