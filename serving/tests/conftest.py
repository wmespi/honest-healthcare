"""Shared fixtures for the serving tests.

`api_data` writes a small, coherent set of RAW Parquet (rates + provider-group
rosters + the NPPES / NUCC / RBCS / CMS reference tables) under a temp DATA_DIR,
runs `build.build.build()` against it exactly as `make build` would (so the
fixture and the real pipeline exercise the same code), and points the FastAPI
app at the resulting `data/serving/` via a `TestClient`. Schemas track
docs/schema.md; the row values are chosen so every contract assertion in
test_api_contract.py has something real to check.

The other test files here (test_cms_utilization, test_specialty_profiles,
test_mpfs, test_doctors_clinicians) run the reference/ builders against their own
data-test/ paths (repo-root relative) and ignore this fixture.

`corpus` is a smaller one of its own for the tests in inputs/ and tables/,
which build the new serving tables (build/tables/) from it.
"""
import os
import subprocess
import sys

import duckdb
import pytest
import yaml

# DATA_DIR / SERVING_DIR must be set before `serving.*` is imported
# (data_sources.py freezes the glob paths at import time). conftest.py is
# loaded before any test module. Repo-root relative (not "/app/…") so this
# also runs on the host under `make test LOCAL=1` — in the container the root
# resolves to /app. GH #59.
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIX_DIR = os.path.join(_REPO, "data-test", "apifix")
os.environ["DATA_DIR"] = FIX_DIR
os.environ["SERVING_DIR"] = f"{FIX_DIR}/serving"

# `cd serving && pytest tests/` (make test) puts serving/ on sys.path but not
# the repo root — test_build.py imports `build.build` (which in turn imports
# `serving.*` and `reference.*`). Prepend the root so it resolves everywhere.
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

BLUE_VALUE = "GA Blue Value HIX Individual Network"
OPEN_ACCESS = "GA Blue Open Access POS Network"
FILE_ID = 1


def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s[:100].strip("-") or "_unattributed"


def _parquet(con, path: str, schema: dict, rows: list[tuple]):
    """Write `rows`, Python values in `schema`'s column order, to `path` with
    `schema`'s types. An empty list writes the columns and no rows."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con.execute("CREATE OR REPLACE TEMP TABLE t (" + ", ".join(f"{c} {t}" for c, t in schema.items()) + ")")
    if rows:  # literals in one statement: executemany and long parameter lists are both slow
        con.execute("INSERT INTO t VALUES " + ", ".join("(" + ", ".join(map(_literal, r)) + ")" for r in rows))
    con.execute(f"COPY t TO '{path}' (FORMAT parquet)")


def _literal(v):
    """A Python value as a SQL literal; the table's column type casts it."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def _write(con, path: str, columns: str, rows: list[tuple]):
    """Each cell is a raw SQL fragment: "'text'" for strings, an int/float as-is,
    "CAST(1 AS BOOLEAN)" for bools."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rows:
        raise ValueError(f"no rows for {path}")
    body = ", ".join("(" + ", ".join(str(c) for c in row) + ")" for row in rows)
    con.execute(
        f"COPY (SELECT * FROM (VALUES {body}) AS t({columns})) "
        f"TO '{path}' (FORMAT parquet)"
    )


def _build(data_dir: str) -> None:
    a = f"{data_dir}/anthem"
    con = duckdb.connect()

    # ── codes ────────────────────────────────────────────────────────────────
    _write(con, f"{a}/codes/1.parquet",
           "billing_code_type, billing_code, name, description", [
               ("'CPT'", "'99213'", "'Office visit, est. patient'", "'Office/outpatient visit est'"),
               ("'CPT'", "'70450'", "'CT head/brain w/o contrast'", "'Ct head/brain w/o dye'"),
               ("'CPT'", "'45378'", "'Diagnostic colonoscopy'", "'Colonoscopy diagnostic'"),
               ("'CPT'", "'93000'", "'Electrocardiogram, complete'", "'Electrocardiogram complete'"),
               ("'CPT'", "'90837'", "'Psychotherapy, 60 min'", "'Psytx w pt 60 minutes'"),
           ])

    # ── group_sets: gs 100 -> pg{10,20}; gs 200 -> pg{30}; gs 300 -> pg{10,20,30,40};
    # gs 400 -> pg{60} — Ng's own group, priced separately from Baker's so a
    # cost-sort test (#87 follow-up) has two PCP providers with different rates.
    # gs 500 -> pg{70,80,90,95}: radiologists billing under their own NPIs, priced for 70450 only.
    gs_rows = []
    for gs, pgs in ((100, (10, 20)), (200, (30,)), (300, (10, 20, 30, 40)), (400, (60,)),
                    (500, (70, 80, 90, 95))):
        for pg in pgs:
            gs_rows.append((FILE_ID, gs, pg))
    _write(con, f"{a}/group_sets/1.parquet",
           "file_id, group_set_id, provider_group_id",
           [tuple(map(str, r)) for r in gs_rows])

    # ── providers: NPI -> provider_group membership + network ────────────────
    # 1000000001 cardiologist, ...02 internal med, ...03 clinical social worker,
    # ...04 radiologist, 1000000005 hospital org NPI.
    # provider_group_id → (network, npi). Groups 10 & 20 bill under the same org
    # NPI (tin_value 1000000010) — a two-doctor practice split across two
    # provider-reference buckets, which must fold into one practice.
    prov = [
        (10, BLUE_VALUE, 1000000001, 1000000010), (10, BLUE_VALUE, 1000000002, 1000000010),
        (20, BLUE_VALUE, 1000000003, 1000000010), (20, BLUE_VALUE, 1000000004, 1000000010),
        (30, OPEN_ACCESS, 1000000005, 1000000030), (30, OPEN_ACCESS, 1000000001, 1000000030),
        (40, OPEN_ACCESS, 1000000002, 1000000040),
        # Ng (NP, PCP-eligible) — her own group/group_set, priced separately
        # from Baker's so the two PCP providers can carry different rates.
        (60, BLUE_VALUE, 1000000011, 1000000060),
        # Diaz also bills under his own NPI, and under an NPI with no Georgia
        # NPPES record, as an out-of-state reading group would. Ito, in Diaz's
        # CMS group, and Okafor, in none, bill under their own NPIs.
        (70, BLUE_VALUE, 1000000004, 1000000004),
        (80, BLUE_VALUE, 1000000004, 1999999999),
        (90, BLUE_VALUE, 1000000012, 1000000012),
        (95, BLUE_VALUE, 1000000013, 1000000013),
    ]
    _write(con, f"{a}/providers/1.parquet",
           "file_id, provider_group_id, network_name, npi, tin_type, tin_value",
           [(FILE_ID, pg, f"'{net}'", npi, "'npi'", f"'{tin}'") for pg, net, npi, tin in prov])

    _write(con, f"{a}/npi_lookup.parquet", "npi, tin_value",
           [(n, f"'{t}'") for n, t in
            ((1000000001, 1000000010), (1000000002, 1000000010), (1000000003, 1000000010),
             (1000000004, 1000000010), (1000000005, 1000000030), (1000000011, 1000000060))])

    # ── prices: one row per (network x negotiated price) ─────────────────────
    # cols: file_id group_set_id network_name billing_code_type billing_code
    #       negotiation_arrangement negotiated_type negotiated_rate
    #       expiration_date service_code billing_class modifier setting net
    P = []

    def price(gs, net, code, rate, *, svc="11", mod="", setting="outpatient",
              arr="ffs", ntype="negotiated", cls="professional"):
        P.append((FILE_ID, gs, f"'{net}'", "'CPT'", f"'{code}'", f"'{arr}'",
                  f"'{ntype}'", rate, "'9999-12-31'", f"'{svc}'", f"'{cls}'",
                  f"'{mod}'", f"'{setting}'", f"'{_slug(net)}'"))

    # 99213 — the workhorse. Blue Value cheaper median than Open Access.
    price(100, BLUE_VALUE, "99213", 88.00)
    price(300, BLUE_VALUE, "99213", 92.50)
    price(100, BLUE_VALUE, "99213", 95.00, svc="11|02")
    price(300, OPEN_ACCESS, "99213", 108.00)
    price(200, OPEN_ACCESS, "99213", 121.00)
    # out-of-scope noise the consumer rate views must exclude (outpatient scope):
    # a facility line, an inpatient-only rate, and a "% of charges" row whose
    # negotiated_rate (60.0) is a percentage, not $60.
    price(100, BLUE_VALUE, "99213", 900.00, cls="institutional")
    price(100, BLUE_VALUE, "99213", 500.00, setting="inpatient")
    price(100, BLUE_VALUE, "99213", 60.00, ntype="percentage")
    # in-scope by type but a placeholder: $0.50 is below the sentinel ceiling
    # (~5% of the code's ~$92 median) — the compare / quote views drop it.
    price(100, BLUE_VALUE, "99213", 0.50)
    # 70450 — global + professional (-26) + technical (-TC), office & hosp-outpatient
    price(100, BLUE_VALUE, "70450", 240.00)
    price(100, BLUE_VALUE, "70450", 70.00, mod="26")
    price(100, BLUE_VALUE, "70450", 175.00, mod="TC")
    # a facility-only line for the same practice and scan — never part of an office quote
    price(100, BLUE_VALUE, "70450", 800.00, svc="22")
    price(300, OPEN_ACCESS, "70450", 265.00)
    price(500, BLUE_VALUE, "70450", 300.00)
    # 45378 — ASC + hospital outpatient
    price(300, BLUE_VALUE, "45378", 410.00, svc="24", setting="outpatient")
    price(300, OPEN_ACCESS, "45378", 505.00, svc="22", setting="outpatient")
    # 93000 — EKG, cardiology bread-and-butter
    price(100, BLUE_VALUE, "93000", 14.31)
    price(300, OPEN_ACCESS, "93000", 18.90)
    # 90837 — psychotherapy (behavioral); only via the LCSW's group
    price(100, BLUE_VALUE, "90837", 132.10)
    # 99204 — new-patient PCP visit (#87 follow-up cost-sort). Baker (gs 100,
    # via provider_group 10) is pricier than Ng (gs 400, her own group) — the
    # cheapest-first PCP ranking test needs these to differ. Ng's group also
    # has a *cheaper* $10 rate on 12002 (wound repair, not in the PCP
    # billing-code family) — a decoy: if the family filter were missing, MIN
    # would wrongly pick the $10 row instead of her real $60 99204 rate.
    price(100, BLUE_VALUE, "99204", 150.00)
    price(400, BLUE_VALUE, "99204", 60.00)
    price(400, BLUE_VALUE, "12002", 10.00)
    # 99202 — a *cheaper* new-patient code, also reachable via Baker's own
    # group (gs 100), but not typical for Internal Medicine (no profile row
    # below) and never billed by Baker to Medicare. This is the real bug the
    # plausible-tier fix (#87 follow-up) catches: a naive MIN() would prefer
    # this $38 over Baker's real (typical) $150 99204 rate — reproducing live
    # testing, where a network-wide group-fanout floor for 99202 (reachable
    # by thousands of unrelated NPIs) out-competed a provider's actual menu.
    price(100, BLUE_VALUE, "99202", 38.00)

    os.makedirs(f"{a}/prices", exist_ok=True)
    for net in (BLUE_VALUE, OPEN_ACCESS):
        rows = [r for r in P if r[2] == f"'{net}'"]
        # drop the trailing `net` column from the file — it's the Hive path key
        _write(con, f"{a}/prices/net={_slug(net)}/1.parquet",
               "file_id, group_set_id, network_name, billing_code_type, billing_code, "
               "negotiation_arrangement, negotiated_type, negotiated_rate, expiration_date, "
               "service_code, billing_class, modifier, setting",
               [r[:-1] for r in rows])

    # ── NPPES GA subset ─────────────────────────────────────────────────────
    npp = [
        (1000000001, "individual", "", "Adams", "Carol", "207RC0000X", "Internal Medicine", 0, 0,
         "1968 Peachtree Rd NW", "", "Atlanta", "GA", "30309"),
        (1000000002, "individual", "", "Baker", "David", "207R00000X", "Internal Medicine", 0, 0,
         "550 Peachtree St NE", "", "Atlanta", "GA", "30308"),
        (1000000003, "individual", "", "Carter", "Ellen", "1041C0700X", "Other", 0, 0,
         "12 Clairmont Ave", "Ste 200", "Decatur", "GA", "30030"),
        (1000000004, "individual", "", "Diaz", "Frank", "2085R0202X", "Radiology", 0, 0,
         "1 Baptist Way", "", "Marietta", "GA", "30060"),
        (1000000012, "individual", "", "Ito", "Kenji", "2085R0202X", "Radiology", 0, 0,
         "4 Bay Ave", "", "Savannah", "GA", "31401"),
        (1000000013, "individual", "", "Okafor", "Ada", "2085R0202X", "Radiology", 0, 0,
         "5 Lake Dr", "", "Athens", "GA", "30601"),
        # psychiatrist + neurologist share the NUCC classification "Psychiatry &
        # Neurology", so matching on classification alone would over-match.
        (1000000006, "individual", "", "Evans", "Grace", "2084P0800X", "Psychiatry", 0, 0,
         "2 Mind Way", "", "Atlanta", "GA", "30307"),
        (1000000007, "individual", "", "Foster", "Henry", "2084N0400X", "Neurology", 0, 0,
         "3 Brain Ct", "", "Atlanta", "GA", "30306"),
        (1000000005, "organization", "Emory University Hospital", "", "", "282N00000X", "Hospital", 1, 0,
         "1364 Clifton Rd NE", "", "Atlanta", "GA", "30322"),
        # Nurse Practitioner, Family — PCP-eligible (#83), her own group so a
        # cost-sort ranking can put her below/above Baker on price (#87).
        (1000000011, "individual", "", "Ng", "Priya", "363LF0000X", "Nurse Practitioner", 0, 0,
         "9 Primary Way", "", "Atlanta", "GA", "30310"),
        # org NPIs used as tin_value (the billing practice) — resolve to a name
        (1000000010, "organization", "Peachtree Internal Medicine LLC", "", "", "207R00000X", "Internal Medicine", 0, 1,
         "1 Peachtree St", "", "Atlanta", "GA", "30303"),
        (1000000030, "organization", "Open Access Health Partners", "", "", "193200000X", "Multi-Specialty", 0, 1,
         "5 Access Way", "", "Macon", "GA", "31201"),
    ]
    _write(con, f"{data_dir}/nppes/ga_providers.parquet",
           "npi, entity_type, org_name, last_name, first_name, taxonomy_code, taxonomy_group, "
           "is_hospital, is_clinic, address_line1, address_line2, city, state, postal_code",
           [(n[0], f"'{n[1]}'", f"'{n[2]}'", f"'{n[3]}'", f"'{n[4]}'", f"'{n[5]}'", f"'{n[6]}'",
             f"CAST({n[7]} AS BOOLEAN)", f"CAST({n[8]} AS BOOLEAN)", f"'{n[9]}'", f"'{n[10]}'",
             f"'{n[11]}'", f"'{n[12]}'", f"'{n[13]}'") for n in npp])

    # ── NUCC taxonomy labels ───────────────────────────────────────────────
    nucc = [
        # physician subspecialties: NUCC puts the specialty in Classification
        ("207RC0000X", "Allopathic & Osteopathic Physicians", "Cardiovascular Disease",
         "", "Cardiovascular Disease", "Cardiovascular Disease", 1),
        ("207R00000X", "Allopathic & Osteopathic Physicians", "Internal Medicine",
         "", "Internal Medicine", "Internal Medicine", 1),
        ("1041C0700X", "Behavioral Health & Social Service Providers", "Social Worker",
         "Clinical", "Clinical", "Social Worker, Clinical", 1),
        ("2085R0202X", "Allopathic & Osteopathic Physicians", "Radiology",
         "Diagnostic Radiology", "Diagnostic Radiology", "Diagnostic Radiology", 1),
        ("2084P0800X", "Allopathic & Osteopathic Physicians", "Psychiatry & Neurology",
         "", "Psychiatry", "Psychiatry", 1),
        ("2084N0400X", "Allopathic & Osteopathic Physicians", "Psychiatry & Neurology",
         "", "Neurology", "Neurology", 1),
        ("282N00000X", "Hospitals", "General Acute Care Hospital",
         "", "General Acute Care Hospital", "General Acute Care Hospital", 0),
        ("363LF0000X", "Physician Assistants & Advanced Practice Nursing Providers",
         "Nurse Practitioner", "Family", "Family", "Family", 1),
    ]
    _write(con, f"{data_dir}/reference/nucc_taxonomy.parquet",
           "taxonomy_code, grouping, classification, specialization, display_name, specialty, is_individual",
           [(f"'{c}'", f"'{g}'", f"'{cl}'", f"'{sp}'", f"'{dn}'", f"'{spec}'",
             f"CAST({iv} AS BOOLEAN)") for c, g, cl, sp, dn, spec, iv in nucc])

    # ── RBCS consumer code labels ──────────────────────────────────────────
    lab = [
        ("99213", "Office visit (established patient)", "Evaluation and Management",
         "Office/Outpatient Services", "Office/Outpatient Visits", 1),
        ("70450", "CT scan of the head", "Imaging", "Advanced Imaging", "CT Head", 1),
        ("45378", "Colonoscopy", "Procedure", "Endoscopy", "Colonoscopy", 1),
        ("93000", "Electrocardiogram (EKG)", "Test", "Cardiovascular Testing", "EKG", 0),
        ("90837", "Therapy session (60 minutes)", "Behavioral Health",
         "Psychotherapy", "Psychotherapy", 1),
    ]
    _write(con, f"{data_dir}/reference/code_labels.parquet",
           "billing_code_type, billing_code, short_name, rbcs_category, rbcs_subcategory, "
           "rbcs_family, rbcs_is_major, label, search_text",
           [("'CPT'", f"'{code}'", f"'{sn}'", f"'{cat}'", f"'{sub}'", f"'{fam}'",
             f"CAST({maj} AS BOOLEAN)", f"'{fam}'",
             f"'{(sn + ' ' + cat + ' ' + fam + ' ' + code).lower()}'")
            for code, sn, cat, sub, fam, maj in lab])

    # ── CMS Medicare Part B utilization (evidence Tier 1) ──────────────────
    cms = [
        (1000000001, "93000", "O", 130, 210, 205, 12.40, "Cardiology", "N", 2024),
        (1000000001, "99213", "O", 240, 410, 405, 84.10, "Cardiology", "N", 2024),
        (1000000003, "90837", "O", 60, 410, 405, 118.90, "Clinical Social Worker", "N", 2024),
        (1000000004, "70450", "O", 90, 120, 118, 41.00, "Diagnostic Radiology", "N", 2024),
    ]
    _write(con, f"{data_dir}/cms/ga_provider_service.parquet",
           "npi, hcpcs_cd, place_of_service, tot_benes, tot_srvcs, tot_bene_day_srvcs, "
           "avg_mdcr_alowd_amt, provider_type, hcpcs_drug_ind, year",
           [(n, f"'{c}'", f"'{pos}'", b, s, d, amt, f"'{pt}'", f"'{drug}'", y)
            for n, c, pos, b, s, d, amt, pt, drug, y in cms])

    # ── specialty procedure profiles (evidence Tier 2) ────────────────────
    prof = [
        ("Cardiovascular Disease", "70450", 8, 10, 0.80),
        ("Cardiovascular Disease", "99213", 9, 10, 0.90),
        ("Internal Medicine", "99213", 40, 42, 0.95),
        # 99204 typical for Internal Medicine, but 99202 is not (no row) —
        # the #87 follow-up plausible-tier cost-sort test needs that split.
        ("Internal Medicine", "99204", 38, 42, 0.90),
    ]
    _write(con, f"{data_dir}/reference/specialty_procedure_profiles.parquet",
           "specialty, hcpcs_cd, billers, specialty_providers, prevalence",
           [(f"'{sp}'", f"'{c}'", b, n, p) for sp, c, b, n, p in prof])

    # ── CMS Medicare Physician Fee Schedule benchmark (issue #61) ─────────
    # GA allowed $ per (code × modifier × POS × locality). 99213 across both
    # GA localities → benchmark = median(67.20, 63.36) = 65.28.
    mpfs = [
        ("99213", "CPT", "",   "nonfacility", "01", 67.20, "A"),
        ("99213", "CPT", "",   "facility",    "01", 51.20, "A"),
        ("99213", "CPT", "",   "nonfacility", "99", 63.36, "A"),
        ("99213", "CPT", "",   "facility",    "99", 48.96, "A"),
        ("70450", "CPT", "",   "nonfacility", "01", 166.40, "A"),
        ("70450", "CPT", "26", "nonfacility", "01", 48.00, "A"),
        ("70450", "CPT", "TC", "nonfacility", "01", 118.40, "A"),
        ("93000", "CPT", "",   "nonfacility", "01", 10.88, "R"),
        ("J9299", "HCPCS", "", "nonfacility", "01", None,  "C"),  # carrier-priced flag
    ]
    _write(con, f"{data_dir}/reference/mpfs_ga.parquet",
           "billing_code, billing_code_type, modifier, pos, locality, medicare_allowed, status",
           [(f"'{bc}'", f"'{t}'", f"'{m}'", f"'{p}'", f"'{loc}'",
             "NULL" if amt is None else amt, f"'{st}'")
            for bc, t, m, p, loc, amt, st in mpfs])

    # ── CMS OPPS Addendum B (make reference STEP=opps) — national hospital rate ──
    _write(con, f"{data_dir}/reference/opps.parquet",
           "billing_code, billing_code_type, status_indicator, apc, payment_rate, year",
           [("'70450'", "'CPT'", "'Q3'", "'5522'", 106.81, 2026)])

    # ── CMS Doctors & Clinicians (make reference STEP=doctors-clinicians) ────────────────
    # one row per NPI: real group identity + demographics
    dac = [
        (1000000001, "Adams", "Carol", "M.D.", "Cardiology",
         "G0000001", "Peachtree Cardiology Associates", 2005,
         "Emory University School of Medicine", "F"),
        (1000000002, "Baker", "David", "M.D.", "Internal Medicine",
         "G0000001", "Peachtree Cardiology Associates", 2000,
         "Medical College of Georgia", "M"),
        (1000000003, "Carter", "Ellen", "LCSW", "Clinical Social Worker",
         "G0000002", "Decatur Behavioral Health", 2012,
         "University of Georgia", "F"),
        (1000000004, "Diaz", "Frank", "M.D.", "Diagnostic Radiology",
         "G0000003", "Marietta Imaging Partners", 2008,
         "Duke University School of Medicine", "M"),
        (1000000012, "Ito", "Kenji", "M.D.", "Diagnostic Radiology",
         "G0000003", "Marietta Imaging Partners", 2015,
         "Emory University School of Medicine", "M"),
        (1000000006, "Evans", "Grace", "M.D.", "Psychiatry",
         None, None, 2001, "Yale School of Medicine", "F"),
    ]

    def _s(v):
        return "NULL" if v is None else f"'{v}'"

    _write(con, f"{data_dir}/reference/dac_ga.parquet",
           "npi, last_name, first_name, credential, primary_specialty, "
           "org_pac_id, org_name, grad_year, med_school, gender",
           [(n[0], _s(n[1]), _s(n[2]), _s(n[3]), _s(n[4]), _s(n[5]), _s(n[6]),
             "NULL" if n[7] is None else f"CAST({n[7]} AS INTEGER)", _s(n[8]), _s(n[9]))
            for n in dac])

    # PCP geocodes (make reference STEP=geocode), MIPS (STEP=mips) and ZIP centroids
    # (STEP=zip-centroids). Baker sits ~1 mi from 30309; Ng ~14 mi away, no MIPS.
    _write(con, f"{data_dir}/reference/pcp_geocode.parquet", "npi, latitude, longitude",
           [(1000000002, 33.7900, -84.3900), (1000000011, 33.9500, -84.5500),
            (1000000010, 33.7550, -84.3900)])
    _write(con, f"{data_dir}/reference/mips_ga.parquet",
           "npi, mips_score, mips_source, performance_year",
           [(1000000002, 90.0, "'individual'", 2024)])
    _write(con, f"{data_dir}/reference/zip_centroids.parquet", "zip, lat, lon",
           [("'30309'", 33.7990, -84.3880), ("'30303'", 33.7000, -84.3900)])

    # the CCN↔NPI bridge — many rows per NPI
    affil = [
        (1000000001, "110001", "Emory University Hospital"),
        (1000000001, "110002", "Grady Memorial Hospital"),
        (1000000002, "110001", "Emory University Hospital"),
        (1000000004, "110003", "Northside Hospital"),
    ]
    _write(con, f"{data_dir}/reference/dac_hospital_affiliations.parquet",
           "npi, ccn, facility_name",
           [(n, f"'{c}'", f"'{fn}'") for n, c, fn in affil])

    con.close()

    # the serving tables — run the real build against the raw parquet just
    # written, exactly as `make build` would (no --networks: builds every
    # partition, both BLUE_VALUE and OPEN_ACCESS).
    from build.build import build as _build_serving
    _build_serving(data_dir, f"{data_dir}/serving", networks=None, test=False)


@pytest.fixture(scope="session")
def api_data():
    subprocess.run([sys.executable, "-c",
                    f"import shutil; shutil.rmtree({FIX_DIR!r}, ignore_errors=True)"], check=True)
    _build(FIX_DIR)
    yield FIX_DIR
    import shutil
    shutil.rmtree(FIX_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def api(api_data):
    """A TestClient bound to the app, reading the fixture DATA_DIR in-process."""
    from fastapi.testclient import TestClient
    from serving.main import app
    with TestClient(app) as c:
        yield c


# ── the new serving tables (build/tables/) ───────────────────────────────────

CORPUS_TARGETS = """
targets:
  - name: Blue Value
    exchange_networks: ["45334-GAN001"]
    network_patterns: ["GA Blue Value HIX*"]
  - name: Pathway
    exchange_networks: ["49046-GAN008"]
    network_patterns: ["Pathway PCP Copay Choice*"]
"""


PRICE_SCHEMA = {"file_id": "BIGINT", "group_set_id": "BIGINT", "network_name": "VARCHAR",
                "billing_code_type": "VARCHAR", "billing_code": "VARCHAR",
                "negotiation_arrangement": "VARCHAR", "negotiated_type": "VARCHAR",
                "negotiated_rate": "DOUBLE", "expiration_date": "VARCHAR", "service_code": "VARCHAR",
                "billing_class": "VARCHAR", "modifier": "VARCHAR", "setting": "VARCHAR"}
NPPES_SCHEMA = {"npi": "BIGINT", "entity_type": "VARCHAR", "org_name": "VARCHAR", "last_name": "VARCHAR",
                "first_name": "VARCHAR", "taxonomy_code": "VARCHAR", "taxonomy_group": "VARCHAR",
                "is_hospital": "BOOLEAN", "is_clinic": "BOOLEAN", "address_line1": "VARCHAR",
                "address_line2": "VARCHAR", "city": "VARCHAR", "state": "VARCHAR", "postal_code": "VARCHAR"}
MEDICARE_SCHEMA = {"npi": "BIGINT", "hcpcs_cd": "VARCHAR", "place_of_service": "VARCHAR",
                   "tot_benes": "INTEGER", "tot_srvcs": "DOUBLE", "tot_bene_day_srvcs": "INTEGER",
                   "avg_mdcr_alowd_amt": "DOUBLE", "provider_type": "VARCHAR", "hcpcs_drug_ind": "VARCHAR",
                   "year": "INTEGER"}
HOSPITAL_SCHEMA = {"ccn": "VARCHAR", "name": "VARCHAR", "address": "VARCHAR", "city": "VARCHAR",
                   "state": "VARCHAR", "postal_code": "VARCHAR", "county": "VARCHAR", "phone": "VARCHAR",
                   "hospital_type": "VARCHAR", "ownership": "VARCHAR", "emergency_services": "BOOLEAN",
                   "stars": "INTEGER"}
GEOCODE_SCHEMA = {"address_line1": "VARCHAR", "address_line2": "VARCHAR", "city": "VARCHAR",
                  "state": "VARCHAR", "postal_code": "VARCHAR", "match_type": "VARCHAR",
                  "matched_address": "VARCHAR", "latitude": "DOUBLE", "longitude": "DOUBLE",
                  "benchmark": "VARCHAR", "geocoded_on": "DATE"}
DAC_SCHEMA = {"npi": "BIGINT", "last_name": "VARCHAR", "first_name": "VARCHAR", "credential": "VARCHAR",
              "primary_specialty": "VARCHAR", "org_pac_id": "VARCHAR", "org_name": "VARCHAR",
              "grad_year": "INTEGER", "med_school": "VARCHAR", "gender": "VARCHAR"}


class Corpus:
    """Insurer rates and rosters, the index's plan -> file link, Georgia's
    networks, plans, premiums and hospitals, ZIP centroids, NPPES records,
    Census matches, Medicare's billing and rates, CMS groups, a services file and
    a targets file in a tmp dir."""

    def __init__(self, root):
        self.root = root
        self.targets = f"{root}/targets.yaml"
        self.services_file = f"{root}/services.yaml"
        self.services = []
        self.mpfs = []
        self.opps = []
        self.links = []
        self.plans = []
        self.premiums = []
        self.zips = []
        self.scan_prices = {}
        self.group_sets = {}
        self.rosters = {}
        self.npis = []
        self.medicare_billing = []
        self.hospitals = []
        self.geocodes = []
        self.groups = []
        self.taxonomies = []
        self.pending = {}

    def _queue(self, path, schema, rows):
        """Write `rows` to `path` at the next flush, which build() and inputs() do."""
        self.pending[path] = (schema, rows)

    def flush(self):
        con = duckdb.connect()
        con.execute("SET threads = 1")  # tiny files: one thread writes them faster
        for path, (schema, rows) in self.pending.items():
            _parquet(con, path, schema, rows)
        con.close()
        self.pending = {}

    def scan_price(self, fid, label, group_set, code, all_in=None, scan=None, reading=None,
                   service_code="11"):
        """Add the plan's rates for `code` in file `fid` under `label`, covering the
        rosters in `group_set`: the whole service, the scan (TC) and the reading (26)."""
        rows = self.scan_prices.setdefault((fid, label), [])
        for modifier, rate in (("", all_in), ("TC", scan), ("26", reading)):
            if rate is not None:
                rows.append((fid, group_set, label, "CPT", code, "ffs", "fee schedule", float(rate),
                             "9999-12-31", service_code, "professional", modifier, "outpatient"))
        self._queue(f"{self.root}/anthem/prices/net={_slug(label)}/{fid}-scans.parquet", PRICE_SCHEMA, rows)

    def roster(self, fid, group_set, group, biller, *npis):
        """Put provider group `group`, billing as `biller`, with doctors `npis`, in
        `group_set` of file `fid`."""
        self.group_sets.setdefault(fid, []).append((fid, group_set, group))
        self.rosters.setdefault(fid, []).extend(
            (fid, group, "any network", npi, "npi", biller) for npi in npis)
        self._queue(f"{self.root}/anthem/group_sets/{fid}.parquet",
                    {"file_id": "BIGINT", "group_set_id": "BIGINT", "provider_group_id": "BIGINT"},
                    self.group_sets[fid])
        self._queue(f"{self.root}/anthem/providers/{fid}.parquet",
                    {"file_id": "BIGINT", "provider_group_id": "BIGINT", "network_name": "VARCHAR",
                     "npi": "BIGINT", "tin_type": "VARCHAR", "tin_value": "VARCHAR"}, self.rosters[fid])

    def npi(self, npi, name, address, city, postal_code, entity_type="organization",
            taxonomy="2085R0202X", address_line2=""):
        """Add a Georgia NPPES record. An individual's `name` is a last name."""
        org = entity_type == "organization"
        self.npis.append((npi, entity_type, name if org else None, None if org else name,
                          None if org else "PAT", taxonomy, None, False, False, address,
                          address_line2, city, "GA", postal_code))
        self._queue(f"{self.root}/nppes/ga_providers.parquet", NPPES_SCHEMA, self.npis)

    def billed(self, npi, code, place_of_service):
        """Have Medicare's file show `npi` billing `code` in an office (O) or a facility (F)."""
        self.medicare_billing.append((npi, code, place_of_service, 20, 25.0, 25, 100.0,
                                      "Diagnostic Radiology", "N", 2024))
        self._queue(f"{self.root}/cms/ga_provider_service.parquet", MEDICARE_SCHEMA, self.medicare_billing)

    def hospital(self, ccn, name, address, city, postal_code, phone="(404) 555-0100", stars=None):
        """Add a Georgia hospital to Hospital General Information."""
        self.hospitals.append((ccn, name, address, city, "GA", postal_code, "FULTON", phone,
                               "Acute Care Hospitals", "Government", True, stars))
        self._queue(f"{self.root}/reference/hospitals_ga.parquet", HOSPITAL_SCHEMA, self.hospitals)

    def geocode(self, address, city, postal_code, matched_address, lat, lon, address_line2=""):
        """Add Census's answer for an address it was sent."""
        self.geocodes.append((address, address_line2, city, "GA", postal_code, "Exact", matched_address,
                              lat, lon, "Public_AR_Current", None))
        self._queue(f"{self.root}/reference/geocode_addresses.parquet", GEOCODE_SCHEMA, self.geocodes)

    def group(self, npi, org_name):
        """Put clinician `npi` in the CMS group `org_name`."""
        self.groups.append((npi, "SMITH", "PAT", "MD", "DIAGNOSTIC RADIOLOGY", "1234567890", org_name,
                            2005, "OTHER", "F"))
        self._queue(f"{self.root}/reference/dac_ga.parquet", DAC_SCHEMA, self.groups)

    def taxonomy(self, code, classification, specialization):
        """Add a NUCC specialty code."""
        self.taxonomies.append((code, None, classification, specialization, None, None, False))
        self._queue(f"{self.root}/reference/nucc_taxonomy.parquet",
                    {"taxonomy_code": "VARCHAR", "grouping": "VARCHAR", "classification": "VARCHAR",
                     "specialization": "VARCHAR", "display_name": "VARCHAR", "specialty": "VARCHAR",
                     "is_individual": "BOOLEAN"}, self.taxonomies)

    def inputs(self):
        """A connection with every build-only table made, to read one directly."""
        from build import tables
        self.flush()
        con = tables.connect(f"{self.root}/.spill")
        for table in tables.inputs(self.targets, self.services_file):
            table.make(con, self.root)
        return con

    def plan(self, plan_id, network_id, name, metal_level="Silver"):
        """Add a plan on `network_id` to Georgia's plan file."""
        self.plans.append((plan_id, network_id, name, metal_level))
        self._write_plans()

    def remove_plans(self, network_id):
        """Take every plan on `network_id` out of Georgia's plan file."""
        self.plans = [p for p in self.plans if p[1] != network_id]
        self._write_plans()

    def premium(self, plan_id, rating_area, ages=range(65)):
        """Add `plan_id`'s monthly premium in `rating_area` for each of `ages`."""
        self.premiums += [(plan_id, rating_area, age) for age in ages]
        self._write_premiums()

    def remove_premiums(self, plan_id, ages=range(65)):
        """Take `plan_id`'s premiums for `ages` out of Georgia's premium file."""
        self.premiums = [p for p in self.premiums if not (p[0] == plan_id and p[2] in ages)]
        self._write_premiums()

    def _write_premiums(self):
        self._queue(f"{self.root}/reference/exchange_premiums_ga.parquet",
                    {"plan_id": "VARCHAR", "rating_area": "INTEGER", "age": "BIGINT",
                     "premium": "DOUBLE", "tobacco_premium": "DOUBLE"},
                    [(p, area, age, 300 + 10 * age, 330 + 11 * age) for p, area, age in self.premiums])

    def zip(self, zip_code, lat, lon):
        """Add a ZIP's centroid to the Census file; None writes an empty coordinate."""
        self.zips.append((zip_code, lat, lon))
        self._queue(f"{self.root}/reference/zip_centroids.parquet",
                    {"zip": "VARCHAR", "lat": "DOUBLE", "lon": "DOUBLE"}, self.zips)

    def service(self, service_id, billing_code, **fields):
        """Add a service to the services file, with placeholder text for any field
        not given."""
        self.services.append({"service_id": service_id, "billing_code": billing_code,
                              "name": f"Service {service_id}", "keywords": "scan",
                              "about": "A scan.", "could_change": ["Contrast dye"],
                              "asks": ["Is this everything?"], **fields})
        with open(self.services_file, "w") as f:
            yaml.safe_dump({"services": self.services}, f, sort_keys=False)

    def medicare(self, billing_code, office, facility=None):
        """Medicare's office rate for `billing_code` in each of Georgia's two payment
        areas, as `office`, plus decoys the build must skip (a facility rate and a
        reading-only rate), and its hospital outpatient rate if `facility`."""
        atlanta, rest = office
        self.mpfs += [(billing_code, "", "nonfacility", "01", atlanta),
                      (billing_code, "", "nonfacility", "99", rest),
                      (billing_code, "", "facility", "01", atlanta / 2),
                      (billing_code, "26", "nonfacility", "01", atlanta / 3)]
        self._queue(f"{self.root}/reference/mpfs_ga.parquet",
                    {"billing_code": "VARCHAR", "billing_code_type": "VARCHAR", "modifier": "VARCHAR",
                     "pos": "VARCHAR", "locality": "VARCHAR", "medicare_allowed": "DOUBLE", "status": "VARCHAR"},
                    [(c, "CPT", m, pos, loc, amt, "A") for c, m, pos, loc, amt in self.mpfs])
        if facility is not None:
            self.opps.append((billing_code, facility))
            self._queue(f"{self.root}/reference/opps.parquet",
                        {"billing_code": "VARCHAR", "billing_code_type": "VARCHAR",
                         "status_indicator": "VARCHAR", "apc": "VARCHAR", "payment_rate": "DOUBLE",
                         "year": "INTEGER"},
                        [(c, "CPT", "Q3", "5523", amt, 2026) for c, amt in self.opps])

    def _write_plans(self):
        self._queue(f"{self.root}/reference/exchange_plans_ga.parquet",
                    {"plan_id": "VARCHAR", "network_id": "VARCHAR", "name": "VARCHAR", "carrier": "VARCHAR",
                     "metal_level": "VARCHAR", "needs_referral": "BOOLEAN"},
                    [(p, n, name, "Anthem Blue Cross and Blue Shield", metal, False)
                     for p, n, name, metal in self.plans])

    def link(self, fid, *plan_ids):
        """Have the index link file `fid` to each of `plan_ids`."""
        self.links += [(fid, p) for p in plan_ids]
        self._write_links()

    def unlink(self, fid):
        """Have the index link file `fid` to no plan."""
        self.links = [(f, p) for f, p in self.links if f != fid]
        self._write_links()

    def _write_links(self):
        self._queue(f"{self.root}/anthem/index_file_plans.parquet",
                    {"file_id": "BIGINT", "plan_id": "VARCHAR"}, self.links)

    def rate(self, fid, label):
        """Add one insurer rate in file `fid` under network label `label`."""
        os.makedirs(f"{self.root}/anthem/prices/net={_slug(label)}", exist_ok=True)
        duckdb.connect().execute(f"""
            COPY (SELECT {fid}::BIGINT AS file_id, 1::BIGINT AS group_set_id, '{label}' AS network_name,
                         'CPT' AS billing_code_type, '70551' AS billing_code, 250.0 AS negotiated_rate)
            TO '{self.root}/anthem/prices/net={_slug(label)}/{fid}.parquet' (FORMAT parquet)
        """)

    def remove_rate(self, fid, label):
        os.remove(f"{self.root}/anthem/prices/net={_slug(label)}/{fid}.parquet")

    def add_target(self, text):
        """Add a target to the targets file, as YAML list items."""
        with open(self.targets, "a") as f:
            f.write(text)

    def build(self, out="serving"):
        from build import tables
        self.flush()
        return tables.build(self.root, f"{self.root}/{out}", targets_path=self.targets,
                            services_path=self.services_file)

    def rows(self, table, out="serving"):
        return duckdb.connect().execute(
            f"SELECT * FROM read_parquet('{self.root}/{out}/{table}.parquet')").fetchall()


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    """Blue Value in file 21057, Pathway and an untargeted network in file 701, the
    index linking each file to its network's plans, Georgia's networks, plans and
    premiums, ZIP centroids, two services with Medicare's rates, and eight billers
    priced for a lower-back MRI on Blue Value, each showing one placement rule."""
    monkeypatch.delenv("DUCKDB_TMP", raising=False)
    c = Corpus(str(tmp_path))
    c.rate(21057, BLUE_VALUE)
    c.rate(701, "Pathway PCP Copay Choice")
    c.rate(701, "TRADITIONAL HEALTH PLAN")
    c.link(21057, "45334GA0020002", "45334GA0020008")
    c.link(701, "49046GA0700053")
    _write(duckdb.connect(), f"{c.root}/reference/exchange_networks_ga.parquet",
           "network_id, issuer_id, name", [
               ("'45334-GAN001'", "'45334'", "'Blue Value Individual Network HMO'"),
               ("'49046-GAN008'", "'49046'", "'Pathway PCP Copay Choice'"),
               ("'58081-GAN001'", "'58081'", "'Individual Georgia HMO'"),
           ])
    c.plan("45334GA0020002", "45334-GAN001", "Anthem Silver Blue Value HMO 4000")
    c.plan("45334GA0020008", "45334-GAN001", "Anthem Bronze Blue Value HMO 5000", "Expanded Bronze")
    c.plan("49046GA0700053", "49046-GAN008", "Anthem Bronze Pathway PCP Copay Choice HMO 6000", "Expanded Bronze")
    c.plan("58081GA0010001", "58081-GAN001", "Individual Georgia HMO Silver")
    for plan_id in ("45334GA0020002", "45334GA0020008", "49046GA0700053", "58081GA0010001"):
        c.premium(plan_id, 1)
    c.premium("45334GA0020002", 2)
    c.zip("30308", 33.770959, -84.377736)
    c.zip("30322", 33.794605, -84.325247)
    c.zip("37421", 35.030393, -85.159553)
    c.zip("31602", 30.8774, -83.2799)
    c.zip("30720", 34.7698, -84.9702)
    c.service("mri-lower-back", "72148", name="Lower-back MRI")
    c.service("mammogram-screening", "77067", name="Screening mammogram",
              preventive_note="Usually $0 in network as preventive care.")
    c.medicare("72148", office=(170.0, 190.0), facility=243.77)
    c.medicare("77067", office=(110.0, 120.0))
    # 1000000001 does the MRI in an office. 1000000002 is a registered imaging
    # center whose doctor Medicare shows only in hospitals. 1000000003 only reads,
    # at an office Census didn't match. 1000000004 reads at Grady's address.
    # 1000000005 is a doctor's own NPI in a CMS group, 1000000006 one with no
    # group. 1000000007 has no Georgia record, and 1000000008 no Medicare billing.
    for i in range(1, 9):
        biller = 1000000000 + i
        doctor = biller if i in (5, 6) else 2000000000 + i
        c.roster(21057, i, i, str(biller), doctor)
        c.scan_price(21057, BLUE_VALUE, i, "72148", all_in=500, scan=300, reading=200)
        if i != 8:
            c.billed(doctor, "72148", "F" if i in (2, 3, 4) else "O")
    c.npi(1000000001, "MIDTOWN IMAGING PC", "550 PEACHTREE ST NE", "ATLANTA", "303082233")
    c.npi(1000000002, "HIRAM IMAGING LLC", "4374 ATLANTA HWY", "HIRAM", "301412300", taxonomy="261QR0200X")
    c.npi(1000000003, "SOUTH GEORGIA RADIOLOGY PC", "2501 N PATTERSON ST", "VALDOSTA", "316021234")
    c.npi(1000000004, "GRADY RADIOLOGY GROUP", "80 JESSE HILL JR DR SE", "ATLANTA", "303033031")
    c.npi(1000000005, "SMITH", "1365 CLIFTON RD NE", "ATLANTA", "303221013", entity_type="individual")
    c.npi(1000000006, "JONES", "1 MAIN ST", "MACON", "312011234", entity_type="individual")
    c.npi(1000000008, "NO MEDICARE CLINIC", "2 MAIN ST", "MACON", "312011234")
    c.group(1000000005, "EMORY MEDICAL CARE FOUNDATION")
    for code, classification, specialization in [
            ("261QR0200X", "Clinic/Center", "Radiology"),
            ("261QM1300X", "Clinic/Center", "Multi-Specialty"),
            ("2085R0202X", "Radiology", "Diagnostic Radiology")]:
        c.taxonomy(code, classification, specialization)
    c.hospital("110079", "GRADY MEMORIAL HOSPITAL", "80 JESSE HILL JR DRIVE SE", "ATLANTA", "30303", stars=3)
    c.hospital("110001", "HAMILTON MEDICAL CENTER", "1200 MEMORIAL DRIVE", "DALTON", "30720", stars=4)
    c.geocode("550 PEACHTREE ST NE", "ATLANTA", "303082233", "550 PEACHTREE ST NE, ATLANTA, GA, 30308",
              33.7712, -84.3853)
    c.geocode("4374 ATLANTA HWY", "HIRAM", "301412300", "4374 ATLANTA HWY, HIRAM, GA, 30141", 33.8751, -84.7624)
    c.geocode("80 JESSE HILL JR DR SE", "ATLANTA", "303033031", "80 JESSE HILL JR DR SE, ATLANTA, GA, 30303",
              33.7524, -84.382)
    c.geocode("80 JESSE HILL JR DRIVE SE", "ATLANTA", "30303", "80 JESSE HILL JR DR SE, ATLANTA, GA, 30303",
              33.7524, -84.382)
    c.geocode("1365 CLIFTON RD NE", "ATLANTA", "303221013", "1365 CLIFTON RD NE, ATLANTA, GA, 30322",
              33.792, -84.323)
    (tmp_path / "targets.yaml").write_text(CORPUS_TARGETS)
    c.flush()
    return c
