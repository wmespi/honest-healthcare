"""Golden-answer tests — pin the product's real answers against the LIVE API.

Unlike application/serving/tests/test_api_contract.py (hermetic, a synthetic fixture via TestClient),
this hits an actually-running server (`API_URL`, default
http://localhost:8000/api) and checks its answers against dollar figures and
counts captured from the real corpus. The point is regression, not
correctness: if a later change moves one of these on purpose, recapture it and
say why in that PR; if it moves by accident, this is what's supposed to catch
it before a user does.

Skips cleanly (not failing) when Blue Value isn't loaded or the API isn't
reachable at all. Only `make test-live` (run via docker compose exec, so it
shares the container's live localhost:8000) exercises the real assertions
below; a bare `pytest tests/live` with nothing listening skips all of them.
`make test` never collects this file (pytest.ini's testpaths leave tests/live out): it is not
hermetic and must never gate on which build steps happen to have been run.
"""
import os

import httpx2
import pytest

API_URL = os.environ.get("API_URL", "http://localhost:8000/api")
BLUE_VALUE = "45334-GAN001"
SILVER = "45334GA0020002"  # Anthem Silver Blue Value HMO 4000
GOLD_PATHWAY = "49046GA0700055"  # Anthem Gold Pathway PCP Copay Choice HMO 1200
GOLD_OPEN_PATHWAY = "49046GA0700021"  # Anthem Gold Pathway HMO 1350, on 49046-GAN001
GOLD_GUIDED_ACCESS = "49046GA0700019"  # Anthem Gold Pathway Guided Access HMO 1350, on 49046-GAN003
CATASTROPHIC_PATHWAY = "49046GA0700001"  # Anthem Catastrophic Pathway HMO 10600 First 3 PCP Visits $40
AMBETTER_SILVER = "70893GA0010077"  # Ambetter Standard Silver HMO $6000 $40, on Premier (70893-GAN001)
AMBETTER = ["70893-GAN001", "70893-GAN002", "70893-GAN003", "70893-GAN004", "70893-GAN005"]
ALLIANT_SILVER = "83761GA0110025"  # SoloCare Standard Silver HMO $6000 40%, on 83761-GAN001
ALLIANT = ["83761-GAN001", "83761-GAN002"]
OSCAR_SILVER = "58081GA0010052"  # Silver Classic Standard HMO $6000 $40, on 58081-GAN001
OSCAR_GUIDED_SILVER = "58081GA0010057"  # Silver Classic Standard Guided Care HMO $6000 $40, on 58081-GAN002
OSCAR = ["58081-GAN001", "58081-GAN002"]
NEAR_EMORY = {"plan_id": SILVER, "zip": "30322"}


@pytest.fixture(scope="module")
def client():
    # same 120s budget as the journeys harness.
    with httpx2.Client(base_url=API_URL, timeout=120) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def _require_live_target_corpus(client):
    try:
        r = client.get("/health")
        root = r.json()
    except (httpx2.HTTPError, ValueError) as e:
        pytest.skip(f"no API reachable at {API_URL}: {e}")
        return
    priced = [n["network_id"] for n in root.get("networks", []) if n["prices_status"] != "missing"]
    if r.status_code != 200 or BLUE_VALUE not in priced:
        pytest.skip(f"{BLUE_VALUE} not loaded at {API_URL} — golden values need the real corpus")


def find_care(client, service_id, **params):
    return client.get("/find-care", params={"service_id": service_id, **NEAR_EMORY, **params}).json()


def rates(place):
    """A place's bills as (part, rate, where the rate is from)."""
    return [(p["part"], p["max"], p["source"]) for p in place["parts"]]


def unpriced(client, plan_id):
    """The services with no place within 50 miles of Emory on `plan_id`."""
    return sorted(s["service_id"] for s in client.get("/services").json()["services"]
                  if not find_care(client, s["service_id"], plan_id=plan_id, radius_mi=50)["results"])


def test_the_build_holds(client):
    root = client.get("/health").json()
    assert [(n["network_id"], n["prices_status"]) for n in root["networks"]] == [
        (n, "current") for n in [BLUE_VALUE, "49046-GAN001", "49046-GAN003", "49046-GAN008", *OSCAR, *AMBETTER,
                                 *ALLIANT]]
    assert root["rows"] == {"plans": 118, "services": 288, "families": 17, "places": 11503, "prices": 387805,
                            "providers": 20279}


def test_each_insurer_goes_by_the_name_it_sells_under(client):
    assert {p["carrier"] for p in client.get("/plans").json()} == {
        "Anthem Blue Cross and Blue Shield", "Ambetter from Peach State Health Plan", "Alliant Health Plans",
        "Oscar Health Plan of Georgia"}


def test_the_families_are_in_the_screens_order(client):
    catalog = client.get("/services").json()
    assert [f["family"] for f in catalog["families"]] == [
        "office_visits", "preventive_visits", "urgent_care", "mental_health", "therapy", "lab_tests", "xrays",
        "mammograms", "ultrasound", "ct", "mri", "endoscopy", "eye_procedures", "bone_joint_procedures",
        "skin_procedures", "other_procedures", "stays"]
    assert {s["family"] for s in catalog["services"]} == {f["family"] for f in catalog["families"]}


def test_a_brain_mri_near_emory_on_silver_4000(client):
    body = find_care(client, "mri-brain")
    assert body["radius_mi"] == 5 and len(body["results"]) == 10
    clinic = next(p for p in body["results"] if p["place_id"] == "1396798229")
    assert clinic["name"] == "THE EMORY CLINIC INC"
    assert rates(clinic) == [("all_in", 180.49, "plan")]
    assert clinic["parts"][0]["label"] == "The scan and its reading" and clinic["kind_name"] == "Medical practice"
    assert body["median_max_rate"] == 180.49
    assert body["cost_sharing"] == [{
        "benefit": "advanced_imaging", "label": "CT, PET and MRI scans", "copay": 300.0, "copay_per": None, "copay_limited": False,
        "coinsurance": 50.0,
        "after_deductible": True, "parts": ["all_in", "scan", "reading", "facility"]}]


def test_an_office_visit_near_emory_shows_one_bill_and_both_shares(client):
    body = find_care(client, "visit-return-20")
    clinic = next(p for p in body["results"] if p["place_id"] == "1396798229")
    assert rates(clinic) == [("visit", 90.5, "plan")]
    assert all(p["kind"] != "hospital" for p in body["results"])
    assert [(s["benefit"], s["parts"]) for s in body["cost_sharing"]] == [
        ("primary_care", ["visit"]), ("specialist", ["visit"])]


def test_companies_billing_from_one_office_are_one_place(client):
    # Related companies list one suite downtown as their address, and the place
    # takes the name of the one with rates for the most services.
    office = [p for p in find_care(client, "visit-return-20")["results"]
              if p["address"] == "235 PEACHTREE ST NE, NORTH TOWER, SUITE 2100"]
    assert [(p["place_id"], p["name"]) for p in office] == [("1104875913", "EMERGINET SRMC, LLC")]
    assert office[0]["billers"] == ["CHEROKEE EMERGENCY SERVICES, LLC", "EMERGINET HENRY, LLC",
                                    "EMERGINET SRMC, LLC", "EMERGINET, LLC"]


def test_a_hospital_on_blue_value_shows_its_readers_and_no_fee_of_its_own(client):
    grady = next(p for p in find_care(client, "mri-brain")["results"] if p["place_id"] == "110079")
    assert rates(grady) == [("facility", None, None), ("reading", 66.48, "plan")]
    fee, reading = grady["parts"]
    assert fee["billing_class"] == "institutional" and fee["missing_note"] and fee["missing_explain"]
    assert reading["missing_note"] is None and reading["missing_explain"] is None
    assert grady["max_rate"] is None
    assert grady["billers"] == ["EMORY MEDICAL CARE FOUNDATION INC"]
    assert grady["cash_price"] == 5561.0


def test_a_hospital_on_pathway_shows_the_fee_in_the_plans_file(client):
    grady = next(p for p in find_care(client, "mri-brain", plan_id=GOLD_PATHWAY)["results"] if p["place_id"] == "110079")
    # Two of the reading groups' fee schedules have a doctor known to read it, and the higher shows.
    assert rates(grady) == [("facility", 1171.15, "plan"), ("reading", 143.14, "plan")]
    assert grady["max_rate"] == 1314.29 and grady["several_rates"]


def test_a_hospital_the_plans_file_leaves_out_shows_the_fee_in_its_own_price_list(client):
    emory = next(p for p in find_care(client, "xray-chest", plan_id=GOLD_PATHWAY)["results"] if p["place_id"] == "110010")
    assert emory["name"] == "EMORY UNIVERSITY HOSPITAL"
    assert rates(emory) == [("facility", 34.58, "hospital"), ("reading", None, None)]
    assert emory["max_rate"] is None and emory["billers"] == [] and emory["cash_price"] == 289.0


def test_a_hospital_shows_the_surgeon_of_a_group_cms_affiliates_with_it(client):
    # No gastroenterology group is placed in Emory University Hospital's building, but
    # CMS affiliates DeKalb Gastroenterology's doctors with it, so the bill is whole.
    body = find_care(client, "colonoscopy-screening-average-risk", plan_id=GOLD_PATHWAY)
    emory = next(p for p in body["results"] if p["place_id"] == "110010")
    assert rates(emory) == [("facility", 2678.54, "hospital"), ("surgeon", 384.81, "plan")]
    assert emory["max_rate"] == 3063.35 and "DEKALB GASTROENTEROLOGY ASSOCIATES" in emory["billers"]
    # A practice never bills the facility's fee, so it says the facility bills its own.
    clinic = next(p for p in body["results"] if p["place_id"] == "1396798229")
    assert rates(clinic) == [("facility", None, None), ("surgeon", 231.21, "plan")]
    assert clinic["parts"][0]["missing_note"] == "Plus the facility’s own fee"


def test_alliant_prices_a_hospitals_fee_in_its_own_file(client):
    # Piedmont Hospital's own price list names no exchange plan, and Alliant's file
    # gives its fee, so the bill is whole.
    body = find_care(client, "colonoscopy-screening-average-risk", plan_id=ALLIANT_SILVER)
    piedmont = next(p for p in body["results"] if p["place_id"] == "110083")
    assert rates(piedmont) == [("facility", 2795.0, "plan"), ("surgeon", 447.09, "plan")]
    assert piedmont["max_rate"] == 3242.09


@pytest.mark.parametrize("plan_id, surgeon", [(OSCAR_SILVER, 715.87), (OSCAR_GUIDED_SILVER, 444.41)])
def test_each_oscar_network_prices_a_hospital_from_its_own_file(client, plan_id, surgeon):
    # Oscar's two networks share one product ID, and each is priced by its own
    # file, which gives Emory University Hospital's fee and its own surgeons' fees.
    body = find_care(client, "colonoscopy-screening-average-risk", plan_id=plan_id)
    emory = next(p for p in body["results"] if p["place_id"] == "110010")
    assert rates(emory) == [("facility", 2896.0, "plan"), ("surgeon", surgeon, "plan")]


@pytest.mark.parametrize("plan_id", [GOLD_OPEN_PATHWAY, GOLD_GUIDED_ACCESS])
def test_every_pathway_network_has_the_hospitals_fees(client, plan_id):
    # Its own file gives Grady's fee, and Emory's own file gives Emory's under the
    # name it uses for every Pathway network.
    grady = next(p for p in find_care(client, "mri-brain", plan_id=plan_id)["results"] if p["place_id"] == "110079")
    assert rates(grady) == [("facility", 1171.15, "plan"), ("reading", 143.14, "plan")]
    emory = next(p for p in find_care(client, "xray-chest", plan_id=plan_id)["results"] if p["place_id"] == "110010")
    assert rates(emory) == [("facility", 34.58, "hospital"), ("reading", None, None)]


def test_a_catastrophic_plan_charges_its_copay_for_its_first_visits_only(client):
    body = find_care(client, "visit-return-20", plan_id=CATASTROPHIC_PATHWAY, specialty="primary_care")
    assert body["cost_sharing"] == [{
        "benefit": "primary_care", "label": "Primary care visit", "copay": 40.0, "copay_per": None, "coinsurance": 0.0,
        "after_deductible": True, "copay_limited": True, "parts": ["visit"]}]


def test_a_brain_mri_carries_medicares_rates(client):
    brain = next(s for s in client.get("/services").json()["services"] if s["service_id"] == "mri-brain")
    assert brain["medicare_rate"] == 185.25 and brain["medicare_facility_rate"] == 243.77


@pytest.mark.parametrize("plan_id", [GOLD_PATHWAY, GOLD_OPEN_PATHWAY, GOLD_GUIDED_ACCESS])
def test_pathway_prices_every_service_near_emory_but_one_only_far_hospitals_price(client, plan_id):
    # Only hospitals' own files price it, and on Pathway none within 50 miles.
    assert unpriced(client, plan_id) == ["joint-knee-scope-loose-body"]


def test_blue_value_leaves_out_what_no_doctor_near_emory_shows_billing(client):
    # Its file has a rate for each, but no doctor Medicare or Medicaid shows billing
    # it near Emory, and it lists no facility's fee, so many procedures (rare
    # variants, and adult care Medicare is too old for and Medicaid seldom covers)
    # have no row.
    gaps = unpriced(client, SILVER)
    assert len(gaps) == 55
    # Nor does its file price a hospital stay.
    assert {"stay-vaginal-delivery", "stay-c-section", "stay-knee-replacement", "stay-hip-replacement"} <= set(gaps)
    assert {"checkup-return-18-39", "counseling-family-with", "therapy-iontophoresis", "surgery-tonsils-adult",
            "surgery-vasectomy"} <= set(gaps)
    # Medicaid shows who does children's and pregnancy care.
    assert not {"colonoscopy", "eye-cataract", "joint-knee-scope-meniscus", "surgery-gallbladder-scope",
                "skin-biopsy", "surgery-tonsils-adenoids-child", "surgery-ear-tubes", "surgery-fetal-test",
                "counseling-group"} & set(gaps)


def test_a_brain_mri_near_emory_on_ambetter(client):
    body = find_care(client, "mri-brain", plan_id=AMBETTER_SILVER)
    northside = next(p for p in body["results"] if p["place_id"] == "1487636445")
    assert northside["name"] == "NORTHSIDE RADIOLOGY ASSOCIATES LLC"
    assert rates(northside) == [("all_in", 177.17, "plan")]
    assert [(s["benefit"], s["copay"], s["coinsurance"], s["after_deductible"]) for s in body["cost_sharing"]] == [
        ("advanced_imaging", None, 40.0, True)]


def test_ambetter_prices_what_its_file_gives_only_with_a_modifier_or_a_mislabeled_setting(client):
    # Cataract surgery only for the right or left eye, therapy only under a
    # therapist's plan of care, and a stay's MS-DRG under the outpatient setting.
    # Emory's facility fee is from its own file, under its Ambetter exchange name, and
    # the surgeon's is the highest of the groups whose surgeons CMS affiliates with it.
    emory = next(p for p in find_care(client, "eye-cataract", plan_id=AMBETTER_SILVER)["results"]
                 if p["place_id"] == "110010")
    assert rates(emory) == [("facility", 4907.38, "hospital"), ("surgeon", 938.06, "plan")]
    therapy = next(p for p in find_care(client, "therapy-exercise", plan_id=AMBETTER_SILVER)["results"]
                   if p["place_id"] == "1356494686")
    assert rates(therapy) == [("therapy", 24.69, "plan")]
    piedmont = next(p for p in find_care(client, "stay-vaginal-delivery", plan_id=AMBETTER_SILVER)["results"]
                    if p["place_id"] == "110083")
    assert rates(piedmont) == [("stay", 9956.9, "plan")]


def test_ambetter_leaves_out_what_no_doctor_near_emory_shows_billing(client):
    # Hospitals' own files price its facility fees, so what's left is mostly labs and counseling.
    gaps = unpriced(client, AMBETTER_SILVER)
    assert len(gaps) == 13
    assert {"counseling-group", "lab-hep-c"} <= set(gaps)
    assert not {"therapy-massage", "counseling-family-with", "eye-cataract", "therapy-exercise", "surgery-vein-radiofrequency", "surgery-hernia-groin-open",
                "joint-knee-scope-meniscus", "stay-vaginal-delivery", "stay-knee-replacement"} & set(gaps)


def test_quality_shows_only_where_a_cms_measure_fits(client):
    measured = {f["family"] for f in client.get("/services").json()["families"] if f["measured"]}
    assert measured == {"mammograms", "ct", "endoscopy", "eye_procedures", "bone_joint_procedures", "skin_procedures",
                        "other_procedures"}
    body = find_care(client, "visit-return-20", specialty="primary_care")
    assert all(p["quality"] == [] for p in body["results"])


def test_a_search_needs_a_plan(client):
    assert client.get("/find-care", params={"service_id": "mri-brain", "zip": "30322"}).status_code == 422
