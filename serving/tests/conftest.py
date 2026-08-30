"""Shared fixtures for the serving tests.

`api_data` builds a small, coherent Parquet dataset (rates + provider-group
rosters + the NPPES / NUCC / RBCS / CMS reference tables) under a temp DATA_DIR
and points the FastAPI app at it via a `TestClient`. Schemas track
docs/schema.md; the row values are chosen so every contract assertion in
test_api_contract.py has something real to check.

The other test files here (test_cms_utilization, test_specialty_profiles) use
hard-coded /app/data-test paths and ignore this.
"""
import os
import subprocess
import sys

import duckdb
import pytest

# DATA_DIR must be set before `serving.*` is imported (data_sources.py freezes
# the glob paths at import time). conftest.py is loaded before any test module.
FIX_DIR = "/app/data-test/apifix"
os.environ["DATA_DIR"] = FIX_DIR

BLUE_VALUE = "GA Blue Value HIX Individual Network"
OPEN_ACCESS = "GA Blue Open Access POS Network"
FILE_ID = 1


def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s[:100].strip("-") or "_unattributed"


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

    # ── group_sets: gs 100 -> pg{10,20}; gs 200 -> pg{30}; gs 300 -> pg{10,20,30,40}
    gs_rows = []
    for gs, pgs in ((100, (10, 20)), (200, (30,)), (300, (10, 20, 30, 40))):
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
    # provider-reference buckets; /rates/providers must fold them into one row.
    prov = [
        (10, BLUE_VALUE, 1000000001, 1000000010), (10, BLUE_VALUE, 1000000002, 1000000010),
        (20, BLUE_VALUE, 1000000003, 1000000010), (20, BLUE_VALUE, 1000000004, 1000000010),
        (30, OPEN_ACCESS, 1000000005, 1000000030), (30, OPEN_ACCESS, 1000000001, 1000000030),
        (40, OPEN_ACCESS, 1000000002, 1000000040),
    ]
    _write(con, f"{a}/providers/1.parquet",
           "file_id, provider_group_id, network_name, npi, tin_type, tin_value",
           [(FILE_ID, pg, f"'{net}'", npi, "'npi'", f"'{tin}'") for pg, net, npi, tin in prov])

    _write(con, f"{a}/npi_lookup.parquet", "npi, tin_value",
           [(n, f"'{t}'") for n, t in
            ((1000000001, 1000000010), (1000000002, 1000000010), (1000000003, 1000000010),
             (1000000004, 1000000010), (1000000005, 1000000030))])

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
    price(100, BLUE_VALUE, "70450", 175.00, mod="TC", svc="22", setting="outpatient")
    price(300, OPEN_ACCESS, "70450", 265.00)
    # 45378 — ASC + hospital outpatient
    price(300, BLUE_VALUE, "45378", 410.00, svc="24", setting="outpatient")
    price(300, OPEN_ACCESS, "45378", 505.00, svc="22", setting="outpatient")
    # 93000 — EKG, cardiology bread-and-butter
    price(100, BLUE_VALUE, "93000", 14.31)
    price(300, OPEN_ACCESS, "93000", 18.90)
    # 90837 — psychotherapy (behavioral); only via the LCSW's group
    price(100, BLUE_VALUE, "90837", 132.10)

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
        (1000000005, "organization", "Emory University Hospital", "", "", "282N00000X", "Hospital", 1, 0,
         "1364 Clifton Rd NE", "", "Atlanta", "GA", "30322"),
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
        ("282N00000X", "Hospitals", "General Acute Care Hospital",
         "", "General Acute Care Hospital", "General Acute Care Hospital", 0),
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
    ]
    _write(con, f"{data_dir}/reference/specialty_procedure_profiles.parquet",
           "specialty, hcpcs_cd, billers, specialty_providers, prevalence",
           [(f"'{sp}'", f"'{c}'", b, n, p) for sp, c, b, n, p in prof])

    con.close()

    # browse-layer summary — build it from the parquet just written, exactly as
    # `make build-summary` would (also exercises scripts/build_rate_summary.py).
    r = subprocess.run(
        [sys.executable, "/app/scripts/build_rate_summary.py", "--data-dir", data_dir],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"build_rate_summary failed:\n{r.stdout}\n{r.stderr}"


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
    """A TestClient bound to the app, reading the fixture DATA_DIR in-process.
    (test_coverage.py keeps its own `client` fixture — that one hits a live
    server with the full data/ mounted.)"""
    from fastapi.testclient import TestClient
    from serving.main import app
    with TestClient(app) as c:
        yield c
