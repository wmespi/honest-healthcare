"""Shared fixtures for the serving tests.

`corpus` is a small, coherent set of raw and reference Parquet in a tmp dir —
insurer rates and rosters, Georgia's plans, NPPES, Census, Medicare and the
rest — for the tests in inputs/ and tables/, which build the find-care tables
(build/tables/) from it. `api_data` builds the default corpus with `make
build`'s own build() into FIX_DIR/serving, and `api` binds a `TestClient` to
the app reading it.

The other test files here (test_cms_utilization, test_mpfs,
test_doctors_clinicians and the like) run the reference/ builders
against their own data-test/ paths (repo-root relative) and ignore these.
"""
import os
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
# `reference.*`). Prepend the root so it resolves everywhere.
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

BLUE_VALUE = "GA Blue Value HIX Individual Network"


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


@pytest.fixture(scope="session")
def api_data():
    """The default corpus, built into FIX_DIR/serving by `make build`'s own
    build(), which the app reads."""
    import shutil
    from build.build import build
    shutil.rmtree(FIX_DIR, ignore_errors=True)
    c = default_corpus(f"{FIX_DIR}/corpus")
    build(c.root, f"{FIX_DIR}/serving", c.targets, c.services_file)
    yield FIX_DIR
    shutil.rmtree(FIX_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def api(api_data):
    """A TestClient bound to the app, reading the fixture DATA_DIR in-process."""
    from fastapi.testclient import TestClient
    from serving.main import app
    with TestClient(app) as c:
        yield c


# ── the new serving tables (build/tables/) ───────────────────────────────────

# Two of Georgia's service categories, as its cost-sharing file names them.
IMAGING = "Imaging (CT/PET Scans, MRIs)"
PREVENTIVE = "Preventive Care/Screening/Immunization"

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
    networks, plans, premiums, cost sharing and hospitals, ZIP centroids, NPPES records,
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
        self.scores = []
        self.benefits = {}
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

    def roster(self, fid, group_set, group, biller, *npis, tin_type="npi"):
        """Put provider group `group`, billing as `biller`, with doctors `npis`, in
        `group_set` of file `fid`."""
        self.group_sets.setdefault(fid, []).append((fid, group_set, group))
        self.rosters.setdefault(fid, []).extend(
            (fid, group, "any network", npi, tin_type, biller) for npi in npis)
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
                          None if org else "PAT", taxonomy, None, taxonomy.startswith("28"),
                          taxonomy.startswith("261Q"), address, address_line2, city, "GA", postal_code))
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

    def taxonomy(self, code, classification, specialization, display_name=None):
        """Add a NUCC specialty code."""
        self.taxonomies.append((code, None, classification, specialization, display_name, None, False))
        self._queue(f"{self.root}/reference/nucc_taxonomy.parquet",
                    {"taxonomy_code": "VARCHAR", "grouping": "VARCHAR", "classification": "VARCHAR",
                     "specialization": "VARCHAR", "display_name": "VARCHAR", "specialty": "VARCHAR",
                     "is_individual": "BOOLEAN"}, self.taxonomies)

    def score(self, npi, score):
        """Give clinician `npi` Medicare's quality (MIPS) score `score`."""
        self.scores.append((npi, score))
        self._queue(f"{self.root}/reference/mips_ga.parquet",
                    {"npi": "BIGINT", "mips_score": "DOUBLE", "mips_source": "VARCHAR",
                     "performance_year": "INTEGER"},
                    [(npi, score, "individual", 2024) for npi, score in self.scores])

    def inputs(self):
        """A connection with every build-only table made, to read one directly."""
        from build import tables
        self.flush()
        con = tables.connect(f"{self.root}/.spill")
        for table in tables.inputs(self.targets, self.services_file):
            table.make(con, self.root)
        return con

    def plan(self, plan_id, network_id, name, metal_level="Silver", deductible=4000.0):
        """Add a plan on `network_id` to Georgia's plan file, charging a 40%
        coinsurance after the deductible for every service category the
        services use, and nothing for preventive care."""
        self.plans.append((plan_id, network_id, name, metal_level, deductible))
        self._write_plans()
        for benefit in (IMAGING, PREVENTIVE):
            preventive = benefit == PREVENTIVE
            self.cost_sharing(plan_id, benefit, "No Charge" if preventive else "Not Applicable",
                              "Not Applicable" if preventive else "40.00% Coinsurance after deductible")

    def cost_sharing(self, plan_id, benefit, copay, coinsurance, covered=True):
        """Set what `plan_id` charges the member for `benefit`, in Georgia's wording."""
        self.benefits[(plan_id, benefit)] = (covered, copay, coinsurance)
        self._queue(f"{self.root}/reference/exchange_cost_sharing_ga.parquet",
                    {"plan_id": "VARCHAR", "benefit": "VARCHAR", "covered": "BOOLEAN", "copay": "VARCHAR",
                     "coinsurance": "VARCHAR"},
                    [(p, b, *v) for (p, b), v in self.benefits.items()])

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
                              "service_category": IMAGING, "name": f"Service {service_id}", "keywords": "scan",
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
                     "metal_level": "VARCHAR", "needs_referral": "BOOLEAN", "deductible": "DOUBLE"},
                    [(p, n, name, "Anthem Blue Cross and Blue Shield", metal, False, deductible)
                     for p, n, name, metal, deductible in self.plans])

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
    """The default corpus (default_corpus) in a tmp dir, for one test to change."""
    monkeypatch.delenv("DUCKDB_TMP", raising=False)
    return default_corpus(str(tmp_path))


def default_corpus(root):
    """Blue Value in file 21057, Pathway and an untargeted network in file 701, the
    index linking each file to its network's plans, Georgia's networks, plans,
    premiums and cost sharing, ZIP centroids, two services with Medicare's rates,
    and eight billers priced for a lower-back MRI on Blue Value, each showing one
    placement rule."""
    c = Corpus(root)
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
    c.plan("45334GA0020008", "45334-GAN001", "Anthem Bronze Blue Value HMO 5000", "Expanded Bronze", 5000.0)
    c.plan("49046GA0700053", "49046-GAN008", "Anthem Bronze Pathway PCP Copay Choice HMO 6000", "Expanded Bronze",
           6000.0)
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
    c.service("mammogram-screening", "77067", name="Screening mammogram", service_category=PREVENTIVE,
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
            ("261QM1200X", "Clinic/Center", "Magnetic Resonance Imaging (MRI)"),
            ("261QR0200X", "Clinic/Center", "Radiology"),
            ("261QR0206X", "Clinic/Center", "Radiology, Mammography"),
            ("261QR0207X", "Clinic/Center", "Radiology, Mobile Mammography"),
            ("261QR0208X", "Clinic/Center", "Radiology, Mobile"),
            ("261QM1300X", "Clinic/Center", "Multi-Specialty")]:
        c.taxonomy(code, classification, specialization)
    c.taxonomy("2085R0202X", "Radiology", "Diagnostic Radiology", "Diagnostic Radiology Physician")
    c.score(1000000005, 87.5)
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
    c.geocode("1200 MEMORIAL DRIVE", "DALTON", "30720", "1200 MEMORIAL DR, DALTON, GA, 30720", 34.7698, -84.9702)
    with open(c.targets, "w") as f:
        f.write(CORPUS_TARGETS)
    c.flush()
    return c
