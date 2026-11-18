"""The build's test corpus: a small, coherent set of the datasets ingestion
writes, in a folder (payer rates and rosters by network, Georgia's plans,
NPPES, Census, Medicare, hospitals' own price files and the rest), with methods
to add and remove rows, plus the services file and the reviewed list of
hospitals' plan names the build reads. Each file has its dataset's columns
(integration/inputs/datasets.py). The build's tests (inputs/, tables/) run the
real find-care build on it, and application/serving/tests builds the app's
fixture tables from `default_corpus`.
"""
import copy
import json
import os

import duckdb
import yaml

from integration.inputs import datasets as d

# The two networks the corpus prices, by Georgia's network ID.
BLUE_VALUE, PATHWAY = "45334-GAN001", "49046-GAN008"
# When the payer updated the copy of its file the corpus's rates are from.
OCT31 = "2026-10-31 09:24:31+00"


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


# Georgia's service categories, as its cost-sharing file names them, for the
# benefits the test families use.
IMAGING = "Imaging (CT/PET Scans, MRIs)"
PREVENTIVE = "Preventive Care/Screening/Immunization"
PRIMARY_CARE = "Primary Care Visit to Treat an Injury or Illness"
SPECIALIST = "Specialist Visit"

_SCAN_BILLS = [
    {"part": "all_in", "label": "The scan and its reading", "billing_class": "professional", "modifier": "",
     "on_site": True},
    {"part": "scan", "label": "The scan", "billing_class": "professional", "modifier": "TC", "on_site": True,
     "missing": {"note": "Missing the cost of the scan", "explain": "The place that does your scan bills it."}},
    {"part": "reading", "label": "Radiologist's reading", "billing_class": "professional", "modifier": "26",
     "on_site": False,
     "missing": {"note": "Missing the reading", "explain": "A doctor reads your scan and bills for it.",
                 "explain_at_hospital": "Ask the hospital who will read yours."}},
    {"part": "facility", "label": "Hospital facility fee", "billing_class": "institutional",
     "missing": {"note": "Missing the hospital's fee", "explain": "Hospitals bill their own fee."}},
]
_SCAN = {"about": "A scan.", "could_change": ["Contrast dye"], "asks": ["Is this everything?"],
         "bills": _SCAN_BILLS, "hospital": ["facility", "reading"], "elsewhere": [["all_in"], ["scan", "reading"]]}
# What the corpus's families.yaml says: the imaging families the tests were
# written for, and one one-bill family, office visits, not shown at hospitals.
FAMILIES = {
    "extra_bills": {
        "anesthesia": {"label": "Anesthesia", "note": "The facility's group bills it.",
                       "missing": "Ask the facility which group they use."},
        "pathology": {"label": "Lab exam of removed tissue", "note": "The lab bills it.",
                      "missing": "Ask your doctor which lab they use."},
        "doctor": {"label": "The doctor's fee", "note": "Your doctor bills separately.",
                   "missing": "Ask your doctor what they charge."},
    },
    "benefits": {
        "advanced_imaging": {"name": IMAGING, "label": "CT, PET and MRI scans"},
        "preventive": {"name": PREVENTIVE, "label": "Preventive care"},
        "primary_care": {"name": PRIMARY_CARE, "label": "Primary care visit"},
        "specialist": {"name": SPECIALIST, "label": "Specialist visit"},
    },
    "kinds": {"hospital": "Hospital", "imaging_center": "Imaging center", "practice": "Medical practice"},
    "families": [
        {"family": "office_visits", "name": "Office visits", "groups": ["Office E&M - Established"],
         "asks_specialty": True,
         "benefits": ["primary_care", "specialist"], "about": "A visit with a doctor in an office.",
         "could_change": ["Tests done during the visit"], "asks": ["Is the doctor in my network?"],
         "bills": [{"part": "visit", "label": "The visit", "billing_class": "professional", "modifier": "",
                    "on_site": True}],
         "elsewhere": [["visit"]]},
        {"family": "mammograms", "name": "Mammograms", "groups": ["Mammography"], "benefits": ["advanced_imaging"],
         **_SCAN, "centers": {"261QR0200X": "imaging_center", "261QR0208X": "imaging_center",
                              "261QR0206X": "imaging_center", "261QR0207X": "imaging_center"}},
        {"family": "mri", "name": "MRI scans", "groups": ["Magnetic Resonance"], "benefits": ["advanced_imaging"],
         **_SCAN, "centers": {"261QR0200X": "imaging_center", "261QR0208X": "imaging_center",
                              "261QM1200X": "imaging_center"}},
    ],
}
# What the corpus's specialties.yaml says: two of the kinds of doctor someone can
# pick for an office visit, and the NUCC codes that count as each.
SPECIALTIES = {"specialties": [
    {"specialty": "primary_care", "name": "Primary care", "benefit": "primary_care",
     "taxonomies": ["207Q00000X", "207R00000X"]},
    {"specialty": "heart", "name": "Heart", "benefit": "specialist", "taxonomies": ["207RC0000X"]},
    {"specialty": "skin", "name": "Skin", "benefit": "specialist", "taxonomies": ["207N00000X"]},
]}
# What the corpus's measures.yaml says: a hospital measure with a national value, an
# ASC measure CMS labels, one it doesn't, and a clinician's volume.
MEASURES = {"measures": [
    {"measure": "OP-32", "name": "Hospital visits after a colonoscopy", "about": "Lower is better.",
     "subject": "place", "source": "hospital-measures", "unit": "per_1000", "fits": [{"family": "mri"}]},
    {"measure": "OP-39", "name": "Screening recalls", "about": "Normally 5 to 12 percent.", "subject": "place",
     "source": "hospital-measures", "unit": "percent", "fits": [{"family": "mammograms"}]},
    {"measure": "ASC-12", "name": "Hospital visits after a colonoscopy", "about": "Lower is better.",
     "subject": "place", "source": "asc-measures", "unit": "per_1000",
     "fits": [{"family": "mri", "services": ["mri-lower-back"]}]},
    {"measure": "ASC-11", "name": "Better vision", "about": "Higher is better.", "subject": "place",
     "source": "asc-measures", "unit": "percent", "fits": [{"family": "mammograms"}]},
    {"measure": "volume-colonoscopy", "name": "Colonoscopies done", "about": "More suggests practice.",
     "subject": "clinician", "source": "clinician-volume", "unit": "procedures", "category": "Colonoscopy",
     "fits": [{"family": "mri"}]},
]}
GROUPS = {"office_visits": "Office E&M - Established", "mammograms": "Mammography", "mri": "Magnetic Resonance",
          "endoscopy": "Upper GI Endoscopy"}
# A hospital-stay family, which a test adds with Corpus.add_stays.
_STAYS = {
    "family": "stays", "name": "Hospital stays", "benefits": ["primary_care"], "about": "A stay.",
    "could_change": ["Complications"], "asks": ["Which hospital?"],
    "bills": [{"part": "stay", "label": "The hospital's bill for the stay", "billing_class": "institutional",
               "missing": {"note": "Missing the hospital's price", "explain": "Ask the hospital."}}],
    "hospital": ["stay"], "evidence_codes": [], "empty_note": "No hospital shows a price for a stay.",
}
# A procedure family, which a test adds with Corpus.add_endoscopy: the doctor's fee
# in an office or, apart, at a hospital or surgery center, and a facility's own fee.
_ENDOSCOPY = {
    "family": "endoscopy", "name": "Colonoscopy and upper endoscopy", "groups": ["Upper GI Endoscopy"],
    "benefits": ["specialist"], "about": "A scope.", "could_change": ["A biopsy"], "asks": ["Where is it done?"],
    "bills": [
        {"part": "in_office", "label": "The doctor's fee, room included", "billing_class": "professional",
         "modifier": "", "place_of_service": ["11"], "on_site": True},
        {"part": "surgeon", "label": "The doctor's fee", "billing_class": "professional", "modifier": "",
         "place_of_service": ["22|24", "21"], "on_site": False,
         "missing": {"note": "Missing the doctor's fee", "explain": "Ask who will do it."}},
        {"part": "facility", "label": "The facility's fee", "billing_class": "institutional",
         "missing": {"note": "Missing the facility's fee", "explain": "Ask where and what it charges."}}],
    "hospital": ["facility", "surgeon"], "elsewhere": [["in_office"], ["facility", "surgeon"]],
    "centers": {"261QE0800X": "surgery_center"},
}


class Corpus:
    """Payer rates and rosters, Georgia's networks, plans, premiums, cost sharing
    and hospitals, hospitals' own price files, ZIP centroids, NPPES records,
    Census matches, Medicare's billing and rates, CMS groups, a services file and
    a list of hospitals' plan names in a tmp dir."""

    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.services_file = f"{root}/services.yaml"
        self.families_file = f"{root}/families.yaml"
        self.families = json.loads(json.dumps(FAMILIES))
        self.write_families()
        self.specialties_file = f"{root}/specialties.yaml"
        self.specialties = json.loads(json.dumps(SPECIALTIES))
        self.write_specialties()
        self.measures_file = f"{root}/measures.yaml"
        self.measures = json.loads(json.dumps(MEASURES))
        self.write_measures()
        self.hospital_measures = []
        self.hospital_measures_national = []
        self.asc_measures = []
        self.asc_measures_national = []
        self.clinician_volume = []
        self.shoppable = []
        self.hospital_plans_file = f"{root}/hospital_plans.yaml"
        self.hospital_plans([])
        self.hospital_rates = {}
        self.services = []
        self.mpfs = []
        self.opps = []
        self.plans = []
        self.premiums = []
        self.zips = []
        self.rates = []
        self.statuses = {}
        self.group_sets = []
        self.rosters = []
        self.npis = []
        self.locations = []
        self.clinician_locations = []
        self.medicare_billing = []
        self.hospitals = []
        self.geocodes = []
        self.groups = []
        self.taxonomies = []
        self.scores = []
        self.benefits = {}
        self.pending = {}
        self.taxonomy("207Q00000X", "Family Medicine", "", "Family Medicine Physician")
        self.taxonomy("207R00000X", "Internal Medicine", "", "Internal Medicine Physician")
        self.taxonomy("207N00000X", "Dermatology", "", "Dermatology Physician")
        self.taxonomy("207RC0000X", "Internal Medicine", "Cardiovascular Disease",
                      "Cardiovascular Disease Physician")
        self._queue(f"{root}/reference/shoppable_services.parquet", d.SHOPPABLE_SERVICES.columns, self.shoppable)
        # The build reads both of these, so they're written even with no rows.
        self._queue(f"{root}/nppes/ga_locations.parquet", d.NPPES_LOCATIONS.columns, self.locations)
        self._queue(f"{root}/reference/dac_locations_ga.parquet", d.CLINICIAN_LOCATIONS.columns,
                    self.clinician_locations)
        self._write_measure_files()

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

    def rate(self, network_id):
        """Add one payer rate on `network_id`, for a code no service uses, so the
        network is priced."""
        self.rates.append((network_id, 1, "CPT", "70551", "ffs", "negotiated", 250.0, "9999-12-31", "11",
                           "professional", "", "outpatient", True))
        self._write_rates()

    def remove_rates(self, network_id):
        """Take every payer rate on `network_id` away."""
        self.rates = [r for r in self.rates if r[0] != network_id]
        self._write_rates()

    def scan_price(self, network_id, group_set, code, all_in=None, scan=None, reading=None,
                   service_code="11", own_label=True):
        """Add the payer's rates for `code` on `network_id`, covering the rosters in
        `group_set`: the whole service, the scan (TC) and the reading (26)."""
        for modifier, rate in (("", all_in), ("TC", scan), ("26", reading)):
            if rate is not None:
                self.rates.append((network_id, group_set, "CPT", code, "ffs", "fee schedule", float(rate),
                                   "9999-12-31", service_code, "professional", modifier, "outpatient", own_label))
        self._write_rates()

    def modified_price(self, network_id, group_set, code, modifier, rate):
        """Add the payer's rate for `code` billed with `modifier`, which is a list
        joined by `|` when the line names several."""
        self.rates.append((network_id, group_set, "CPT", code, "ffs", "fee schedule", float(rate),
                           "9999-12-31", "11", "professional", modifier, "outpatient", True))
        self._write_rates()

    def facility_price(self, network_id, group_set, code, rate, modifier="", setting="outpatient",
                       negotiated_type="fee schedule", arrangement="ffs", own_label=True, code_type="CPT"):
        """Add the payer's institutional rate for `code` on `network_id`, a
        hospital's own fee, covering the rosters in `group_set`."""
        self.rates.append((network_id, group_set, code_type, code, arrangement, negotiated_type, float(rate),
                           "9999-12-31", "22", "institutional", modifier, setting, own_label))
        self._write_rates()

    def extra_price(self, network_id, code, rate, modifier="", setting="inpatient", negotiated_type="fee schedule"):
        """Add one professional line for `code` on `network_id` that a service's extra bill
        (anesthesia, a lab's exam) reads: a fee schedule line for no doctor in particular."""
        self.rates.append((network_id, 999, "CPT", code, "ffs", negotiated_type, float(rate), "9999-12-31", "21",
                           "professional", modifier, setting, True))
        self._write_rates()

    def _write_rates(self):
        self._queue(f"{self.root}/payer_rates/anthem/rates.parquet", d.PAYER_RATES.columns, self.rates)
        self._write_networks()

    def hospital_price(self, ccn, code, payer=None, plan=None, negotiated=None, cash=None, gross=None,
                       setting="outpatient", billing_class="facility", modifiers=None, code_type="CPT",
                       methodology="fee schedule", percentage=None):
        """Add a row for `code` to hospital `ccn`'s own price file: its rate for
        `payer` and `plan` by `methodology`, or with neither, only its charges."""
        self.hospital_rates.setdefault(ccn, []).append(
            (ccn, code_type, code, setting, billing_class, modifiers, payer, plan, gross, cash, negotiated,
             percentage, None if negotiated is None and percentage is None else methodology))
        self._queue(f"{self.root}/hospital_rates/{ccn}.parquet", d.HOSPITAL_RATES.columns,
                    self.hospital_rates[ccn])

    def hospital_plans(self, groups):
        """Write the reviewed list of what the payer and plan names in hospitals'
        own files mean (integration/hospital_plans.yaml)."""
        with open(self.hospital_plans_file, "w") as f:
            yaml.safe_dump(groups, f, sort_keys=False)

    def network(self, network_id, status, as_of=OCT31, reason=None):
        """Say how fresh `network_id`'s rates are: `current`, `stale` or
        `missing`, from the payer's copy of `as_of`. A network with rates is
        current, from its 31 October copy, unless this says otherwise."""
        self.statuses[network_id] = (status, None if status == "missing" else as_of, reason)
        self._write_networks()

    def _write_networks(self):
        statuses = {r[0]: ("current", OCT31, None) for r in self.rates} | self.statuses
        self._queue(f"{self.root}/payer_rates/anthem/networks.parquet", d.PAYER_NETWORKS.columns,
                    [(n, s, None if s == "missing" else "GA_FILE.json.gz", as_of, reason)
                     for n, (s, as_of, reason) in sorted(statuses.items())])

    def roster(self, network_id, group_set, group, biller, *npis, tin_type="npi"):
        """Put provider group `group`, billing as `biller`, with doctors `npis`, in
        `group_set` on `network_id`. A group already in the set gains the doctors."""
        if (network_id, group_set, group) not in self.group_sets:
            self.group_sets.append((network_id, group_set, group))
        self.rosters.extend((network_id, group, npi, tin_type, biller) for npi in npis)
        self._queue(f"{self.root}/payer_rates/anthem/rate_groups.parquet", d.PAYER_RATE_GROUPS.columns,
                    self.group_sets)
        self._queue(f"{self.root}/payer_rates/anthem/rosters.parquet", d.PAYER_ROSTERS.columns, self.rosters)

    def npi(self, npi, name, address, city, postal_code, entity_type="organization",
            taxonomy="2085R0202X", address_line2="", phone=""):
        """Add a Georgia NPPES record. An individual's `name` is a last name."""
        org = entity_type == "organization"
        self.npis.append((npi, entity_type, name if org else None, None if org else name,
                          None if org else "PAT", taxonomy, None, taxonomy.startswith("28"),
                          taxonomy.startswith("261Q"), address, address_line2, city, "GA", postal_code, phone))
        self._queue(f"{self.root}/nppes/ga_providers.parquet", d.NPPES.columns, self.npis)

    def location(self, npi, address, city, postal_code, phone="", address_line2=""):
        """Add another Georgia address `npi` lists in NPPES as a place it practices."""
        self.locations.append((npi, address, address_line2, city, "GA", postal_code, phone))
        self._queue(f"{self.root}/nppes/ga_locations.parquet", d.NPPES_LOCATIONS.columns, self.locations)

    def practices_at(self, npi, address, city, postal_code, address_line2=""):
        """Have CMS list clinician `npi` practicing at an address."""
        self.clinician_locations.append((npi, address, address_line2, city, "GA", postal_code))
        self._queue(f"{self.root}/reference/dac_locations_ga.parquet", d.CLINICIAN_LOCATIONS.columns,
                    self.clinician_locations)

    def billed(self, npi, code, place_of_service):
        """Have Medicare's file show `npi` billing `code` in an office (O) or a facility (F)."""
        self.medicare_billing.append((npi, code, place_of_service, 20, 25.0, 25, 100.0,
                                      "Diagnostic Radiology", "N", 2024))
        self._queue(f"{self.root}/cms/ga_provider_service.parquet", d.MEDICARE_BILLING.columns, self.medicare_billing)

    def hospital(self, ccn, name, address, city, postal_code, phone="(404) 555-0100", stars=None):
        """Add a Georgia hospital to Hospital General Information."""
        self.hospitals.append((ccn, name, address, city, "GA", postal_code, "FULTON", phone,
                               "Acute Care Hospitals", "Government", True, stars))
        self._queue(f"{self.root}/reference/hospitals_ga.parquet", d.HOSPITALS.columns, self.hospitals)

    def geocode(self, address, city, postal_code, matched_address, lat, lon, address_line2="",
                source="census", located=True):
        """Add a geocoder's answer for an address it was sent: Census's, with its
        standardized address, or OpenStreetMap's, which has none. `located` says
        the point is in Georgia."""
        self.geocodes.append((address, address_line2, city, "GA", postal_code, source, matched_address,
                              lat, lon, located))
        self._queue(f"{self.root}/reference/geocode_addresses.parquet", d.GEOCODES.columns, self.geocodes)

    def group(self, npi, org_name):
        """Put clinician `npi` in the CMS group `org_name`."""
        self.groups.append((npi, "SMITH", "PAT", "MD", "DIAGNOSTIC RADIOLOGY", "1234567890", org_name,
                            2005, "OTHER", "F"))
        self._queue(f"{self.root}/reference/dac_ga.parquet", d.DOCTORS_CLINICIANS.columns, self.groups)

    def taxonomy(self, code, classification, specialization, display_name=None):
        """Add a NUCC specialty code."""
        self.taxonomies.append((code, None, classification, specialization, display_name, None, False))
        self._queue(f"{self.root}/reference/nucc_taxonomy.parquet",
                    d.NUCC_TAXONOMY.columns, self.taxonomies)

    def score(self, npi, score):
        """Give clinician `npi` Medicare's quality (MIPS) score `score`."""
        self.scores.append((npi, score))
        self._queue(f"{self.root}/reference/mips_ga.parquet",
                    d.MIPS.columns, [(npi, score, "individual", 2024) for npi, score in self.scores])

    def inputs(self):
        """A connection with every build-only table made, to read one directly."""
        from integration import tables
        self.flush()
        con = tables.connect(f"{self.root}/.spill")
        for table in tables.inputs(self.services_file, self.hospital_plans_file, self.families_file,
                                   self.specialties_file, self.measures_file):
            table.make(con, self.root)
        return con

    def plan(self, plan_id, network_id, name, metal_level="Silver", deductible=4000.0,
             carrier="Anthem Blue Cross and Blue Shield"):
        """Add a plan on `network_id` to Georgia's plan file, charging a 40%
        coinsurance after the deductible for every service category the
        services use, and nothing for preventive care."""
        self.plans.append((plan_id, network_id, name, metal_level, deductible, carrier))
        self._write_plans()
        for benefit in (IMAGING, PREVENTIVE):
            preventive = benefit == PREVENTIVE
            self.cost_sharing(plan_id, benefit, "No Charge" if preventive else "Not Applicable",
                              "Not Applicable" if preventive else "40.00% Coinsurance after deductible")
        self.cost_sharing(plan_id, PRIMARY_CARE, "$30.00 Copay", "Not Applicable")
        self.cost_sharing(plan_id, SPECIALIST, "$60.00 Copay", "Not Applicable")

    def cost_sharing(self, plan_id, benefit, copay, coinsurance, covered=True):
        """Set what `plan_id` charges the member for `benefit`, in Georgia's wording."""
        self.benefits[(plan_id, benefit)] = (covered, copay, coinsurance)
        self._queue(f"{self.root}/reference/exchange_cost_sharing_ga.parquet",
                    d.EXCHANGE_COST_SHARING.columns, [(p, b, *v) for (p, b), v in self.benefits.items()])

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
                    d.EXCHANGE_PREMIUMS.columns,
                    [(p, area, age, 300 + 10 * age, 330 + 11 * age) for p, area, age in self.premiums])

    def zip(self, zip_code, lat, lon):
        """Add a ZIP's centroid to the Census file; None writes an empty coordinate."""
        self.zips.append((zip_code, lat, lon))
        self._queue(f"{self.root}/reference/zip_centroids.parquet",
                    d.ZIP_CENTROIDS.columns, self.zips)

    def write_families(self):
        """Write `self.families`, the model the build reads as families.yaml."""
        with open(self.families_file, "w") as f:
            yaml.safe_dump(copy.deepcopy(self.families), f, sort_keys=False)

    def write_specialties(self):
        """Write `self.specialties`, the model the build reads as specialties.yaml."""
        with open(self.specialties_file, "w") as f:
            yaml.safe_dump(copy.deepcopy(self.specialties), f, sort_keys=False)

    def write_measures(self):
        """Write `self.measures`, the model the build reads as measures.yaml."""
        with open(self.measures_file, "w") as f:
            yaml.safe_dump(copy.deepcopy(self.measures), f, sort_keys=False)

    def hospital_measure(self, ccn, measure, score, compared=None, national=None, start="2022-01-01",
                         end="2024-12-31"):
        """Add a CMS measure for hospital `ccn`, and `measure`'s national value when given."""
        self.hospital_measures.append((ccn, measure, score, compared, 100, start, end))
        if national is not None and measure not in [r[0] for r in self.hospital_measures_national]:
            self.hospital_measures_national.append((measure, national, start, end))
        self._write_measure_files()

    def asc_measure(self, npi, measure, score, compared=None, national=None, period="2025"):
        """Add a CMS surgery center measure for `npi`, and `measure`'s national value when given."""
        self.asc_measures.append((npi, "11C0001234", measure, score, compared, 100, period))
        if national is not None and measure not in [r[0] for r in self.asc_measures_national]:
            self.asc_measures_national.append((measure, national, None, period))
        self._write_measure_files()

    def volume(self, npi, category, count, displayed=True):
        """Set CMS's procedure volume for clinician `npi` in `category`, `count` as CMS writes it."""
        self.clinician_volume = [r for r in self.clinician_volume if r[:2] != (npi, category)]
        self.clinician_volume.append((npi, category, count, None, displayed, "2026-10-15"))
        self._write_measure_files()

    def _write_measure_files(self):
        for name, dataset, rows in (("hospital_measures_ga", d.HOSPITAL_MEASURES, self.hospital_measures),
                                    ("hospital_measures_national", d.HOSPITAL_MEASURES_NATIONAL,
                                     self.hospital_measures_national),
                                    ("asc_measures_ga", d.ASC_MEASURES, self.asc_measures),
                                    ("asc_measures_national", d.ASC_MEASURES_NATIONAL, self.asc_measures_national),
                                    ("clinician_volume_ga", d.CLINICIAN_VOLUME, self.clinician_volume)):
            self._queue(f"{self.root}/reference/{name}.parquet", dataset.columns, rows)

    def add_stays(self):
        """Add the stays family, a hospital's bill for a stay with no `elsewhere`, and
        its four services: two deliveries, a knee and a hip replacement."""
        self.families["families"].append(_STAYS)
        self.write_families()
        for service_id, drg, doctor, anesthesia in [("stay-vaginal-delivery", "807", "59400", "01967"),
                                                    ("stay-c-section", "788", "59510", "01968"),
                                                    ("stay-knee-replacement", "470", "27447", "01402"),
                                                    ("stay-hip-replacement", "470", "27130", "01214")]:
            self.service(service_id, drg, family="stays", code_type="MS-DRG", doctor=doctor, anesthesia=anesthesia)

    def add_endoscopy(self):
        """Add the procedure family (_ENDOSCOPY), the kind of place it has centers
        of, a generic surgery center as a facility, and the NUCC codes that register
        them."""
        self.families["kinds"]["surgery_center"] = "Surgery center"
        self.families["facilities"] = {"261QA1903X": "surgery_center"}
        self.families["families"].append(copy.deepcopy(_ENDOSCOPY))
        self.write_families()
        self.taxonomy("261QA1903X", "Clinic/Center", "Ambulatory Surgical")
        self.taxonomy("261QE0800X", "Clinic/Center", "Endoscopy")

    def family(self, family, **fields):
        """Change `family`'s fields in the families file."""
        next(f for f in self.families["families"] if f["family"] == family).update(fields)
        self.write_families()

    def service(self, service_id, billing_code, family="mri", **fields):
        """Add a service to `family` in the services file, with a name and keywords
        unless given, and CMS's shoppable code for it in the family's group unless
        the corpus has one."""
        self.services.append((family, {"service_id": service_id, "billing_code": billing_code,
                                       "name": f"Service {service_id}", "keywords": "scan", **fields}))
        self.write_services()
        if "code_type" not in fields and billing_code not in [r[0] for r in self.shoppable]:
            self.shoppable.append((billing_code, "CPT", "Radiology", GROUPS[family], GROUPS[family]))
            self._queue(f"{self.root}/reference/shoppable_services.parquet", d.SHOPPABLE_SERVICES.columns,
                        self.shoppable)

    def write_services(self):
        """Write `self.services`, the model the build reads as services.yaml."""
        listed = {}
        for fam, entry in self.services:
            listed.setdefault(fam, []).append(entry)
        with open(self.services_file, "w") as f:
            yaml.safe_dump(listed, f, sort_keys=False)

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
                    d.MPFS.columns, [(c, "CPT", m, pos, loc, amt, "A") for c, m, pos, loc, amt in self.mpfs])
        if facility is not None:
            self.opps.append((billing_code, facility))
            self._queue(f"{self.root}/reference/opps.parquet",
                        d.OPPS.columns, [(c, "CPT", "Q3", "5523", amt, 2026) for c, amt in self.opps])

    def _write_plans(self):
        self._queue(f"{self.root}/reference/exchange_plans_ga.parquet",
                    d.EXCHANGE_PLANS.columns,
                    [(p, n, name, carrier, metal, False, deductible)
                     for p, n, name, metal, deductible, carrier in self.plans])

    def build(self, out="serving"):
        from integration import tables
        self.flush()
        return tables.build(self.root, f"{self.root}/{out}", self.services_file, self.hospital_plans_file,
                            self.families_file, self.specialties_file, self.measures_file)

    def rows(self, table, out="serving"):
        return duckdb.connect().execute(
            f"SELECT * FROM read_parquet('{self.root}/{out}/{table}.parquet')").fetchall()



def default_corpus(root):
    """Payer rates on Blue Value and Pathway, Georgia's networks (a third with
    no rates), plans, premiums and cost sharing, ZIP centroids, two services
    with Medicare's rates, eight billers priced for a lower-back MRI on Blue
    Value, each showing one placement rule, Grady's own fee for a screening
    mammogram from each source, with its cash prices, and Hamilton's on Pathway,
    where no group reads it."""
    c = Corpus(root)
    c.rate(BLUE_VALUE)
    c.rate(PATHWAY)
    c._queue(f"{c.root}/reference/exchange_networks_ga.parquet", d.EXCHANGE_NETWORKS.columns, [
        (BLUE_VALUE, "45334", "Blue Value Individual Network HMO"),
        (PATHWAY, "49046", "Pathway PCP Copay Choice"),
        ("58081-GAN001", "58081", "Individual Georgia HMO"),
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
    c.service("mri-lower-back", "72148", name="Lower-back MRI", anesthesia="00811", pathology="88305")
    c.service("mammogram-screening", "77067", family="mammograms", name="Screening mammogram",
              benefits=["preventive"], preventive_note="Usually $0 in network as preventive care.")
    c.service("office-visit-established", "99213", family="office_visits", name="Office visit")
    c.medicare("72148", office=(170.0, 190.0), facility=243.77)
    # Pathway's file lists anesthesia, a unit count among it and a both-sides line; Blue Value's
    # lists only the lab's exam, with a percentage and a line with a modifier.
    for rate in (200, 250, 300, 350, 400, 6):
        c.extra_price(PATHWAY, "00811", rate)
    c.extra_price(PATHWAY, "00811", 450, modifier="50")
    c.extra_price(PATHWAY, "00811", 50, negotiated_type="percentage")
    for rate in (30, 40, 50):
        c.extra_price(BLUE_VALUE, "88305", rate, setting="outpatient")
    c.extra_price(BLUE_VALUE, "88305", 20, modifier="26", setting="outpatient")
    c.medicare("77067", office=(110.0, 120.0))
    c.medicare("99213", office=(90.0, 95.0))
    # 1000000001 does the MRI in an office. 1000000002 is a registered imaging
    # center whose doctor Medicare shows only in hospitals. 1000000003 only reads,
    # at an office Census didn't match. 1000000004 reads at Grady's address.
    # 1000000005 is a doctor's own NPI in a CMS group, 1000000006 one with no
    # group. 1000000007 has no Georgia record, and 1000000008 no Medicare billing.
    for i in range(1, 9):
        biller = 1000000000 + i
        doctor = biller if i in (5, 6) else 2000000000 + i
        c.roster(BLUE_VALUE, i, i, str(biller), doctor)
        c.scan_price(BLUE_VALUE, i, "72148", all_in=500, scan=300, reading=200)
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
    # Grady Radiology Group also reads screening mammograms at Grady, on both
    # networks. Grady's own NPI is on Pathway's institutional rate for one, so
    # Pathway's file prices Grady's fee, and Grady's own price file prices it on
    # Blue Value under a name the reviewed list gives that network. The file
    # lists cash prices for both services, and a plan the list says isn't ours.
    c.roster(PATHWAY, 4, 4, "1000000004", 2000000004)
    for network, reading in ((BLUE_VALUE, 35), (PATHWAY, 40)):
        c.scan_price(network, 4, "77067", reading=reading, service_code="22")
    c.billed(2000000004, "77067", "F")
    c.npi(1000000050, "GRADY MEMORIAL HOSPITAL CORPORATION", "80 JESSE HILL JR DR SE", "ATLANTA", "303033031",
          taxonomy="282N00000X")
    c.roster(PATHWAY, 50, 50, "1000000050", 1000000050)
    c.roster(BLUE_VALUE, 50, 50, "1000000050", 1000000050)
    c.facility_price(PATHWAY, 50, "77067", 180)
    c.hospital_plans([{"hospitals": ["110079"], "networks": {BLUE_VALUE: ["ANTHEM BCBS | BLUE VALUE HMO"]},
                       "other": ["ANTHEM BCBS | MEDICARE ADVANTAGE"]}])
    c.hospital_price("110079", "77067", "ANTHEM BCBS", "BLUE VALUE HMO", negotiated=210.0, gross=640.0)
    c.hospital_price("110079", "77067", "ANTHEM BCBS", "MEDICARE ADVANTAGE", negotiated=150.0, gross=640.0)
    c.hospital_price("110079", "77067", gross=640.0, cash=320.0)
    c.hospital_price("110079", "72148", gross=4200.0, cash=1450.0)
    # Pathway's file also prices Hamilton's own fee for a screening mammogram,
    # where no group on file reads it.
    c.npi(1000000060, "HAMILTON HEALTH CARE SYSTEM", "1200 MEMORIAL DRIVE", "DALTON", "30720",
          taxonomy="282N00000X")
    c.roster(PATHWAY, 60, 60, "1000000060", 1000000060)
    c.facility_price(PATHWAY, 60, "77067", 165)
    # CMS's measures: Grady's visits after a colonoscopy with CMS's label, its recalls with only
    # the national value, a hospital with too few cases to say anything, a surgery center that
    # bills as Midtown Imaging, one that bills from Grady's building, and one no biller of ours.
    c.hospital_measure("110079", "OP-32", 13.9, "No Different Than the National Rate")
    c.hospital_measure("110079", "OP-39", 9.5, national=9.0)
    c.hospital_measure("110001", "OP-32", None, "Number of cases too small")
    c.asc_measure(1000000001, "ASC-12", 8.2, "Better Than the National Rate", national=9.9)
    c.asc_measure(1000000001, "ASC-11", 93.0, national=78.8)
    c.asc_measure(1000000004, "ASC-12", 11.0, "No Different Than the National Rate")
    c.asc_measure(1000000999, "ASC-12", 7.0, "Better Than the National Rate")
    c.volume(1000000005, "Colonoscopy", "57")
    c.volume(1000000005, "Hip replacement", "34")
    c.volume(2000000001, "Colonoscopy", "1-10", displayed=False)
    c.volume(3000000001, "Colonoscopy", "20")
    c.flush()
    return c


def office_visit_practice(c):
    """Price an office visit (99213) on Blue Value at a family medicine practice,
    the one place the default corpus's one-bill family is shown."""
    # An office visit is one bill, priced at a practice: a family doctor's own NPI.
    c.npi(1000000070, "FAMILY MEDICINE ASSOCIATES PC", "10 PEACHTREE ST NE", "ATLANTA", "303083001",
          taxonomy="207Q00000X")
    c.geocode("10 PEACHTREE ST NE", "ATLANTA", "303083001", "10 PEACHTREE ST NE, ATLANTA, GA, 30308", 33.7721,
              -84.3855)
    # Its three clinicians: a family doctor, a cardiologist and a radiologist, whose
    # specialty counts as no kind of doctor an office visit asks for.
    for npi, last_name, taxonomy in ((2000000070, "OKAFOR", "207Q00000X"), (2000000071, "HADDAD", "207RC0000X"),
                                     (2000000072, "REYES", "2085R0202X")):
        c.npi(npi, last_name, "10 PEACHTREE ST NE", "ATLANTA", "303083001", entity_type="individual",
              taxonomy=taxonomy)
        c.practices_at(npi, "10 PEACHTREE ST NE", "ATLANTA", "303083001")
    c.roster(BLUE_VALUE, 70, 70, "1000000070", 2000000070, 2000000071, 2000000072)
    c.scan_price(BLUE_VALUE, 70, "99213", all_in=95)
    for npi in (2000000070, 2000000071, 2000000072):
        c.billed(npi, "99213", "O")
    c.flush()
