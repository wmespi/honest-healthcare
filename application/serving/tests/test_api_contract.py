"""API contract tests - every route answered against the app's test tables,
which integration builds from its test corpus (conftest.py, `api` fixture).
Hermetic: no live server, no data/ mount.

Checks response shape and the invariants a consumer relies on. Real-data
assertions live in tests/live/test_golden.py.
"""
import os
import subprocess
import sys

import pytest

from application.serving.routers.find_care import radius_for, wider_radius, with_bill_text

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def test_health_says_when_the_build_ran_and_what_it_holds(api):
    body = api.get("/api/health").json()
    assert body["status"] == "ok" and body["built_at"] is not None
    current = {"prices_status": "current", "prices_as_of": "2026-10-31", "prices_reason": None}
    assert body["networks"] == [
        {"network_id": "45334-GAN001", "name": "Blue Value Individual Network HMO", **current},
        {"network_id": "49046-GAN008", "name": "Pathway PCP Copay Choice", **current}]
    assert body["rows"] == {"plans": 3, "services": 4, "families": 4, "places": 6, "prices": 9, "providers": 4}


def test_health_says_which_networks_have_stale_or_no_prices_and_why(api, monkeypatch):
    from application.serving import main
    monkeypatch.setattr(main, "NETWORKS_SRC", """(FROM (VALUES
        ('45334-GAN001', 'Blue Value', 'stale', DATE '2026-10-31', 'the latest parse of GA_BV.json.gz failed'),
        ('49046-GAN008', 'Pathway', 'missing', NULL, 'GA_PW.json.gz hasn''t been parsed'))
        t(network_id, name, prices_status, prices_as_of, prices_reason))""")
    assert api.get("/api/health").json()["networks"] == [
        {"network_id": "45334-GAN001", "name": "Blue Value", "prices_status": "stale",
         "prices_as_of": "2026-10-31", "prices_reason": "the latest parse of GA_BV.json.gz failed"},
        {"network_id": "49046-GAN008", "name": "Pathway", "prices_status": "missing", "prices_as_of": None,
         "prices_reason": "GA_PW.json.gz hasn't been parsed"}]


def test_health_says_what_is_missing_when_there_is_no_build(api, monkeypatch):
    from application.serving import main
    monkeypatch.setattr(main, "missing_build", lambda: ["prices"])
    r = api.get("/api/health")
    assert r.status_code == 503 and r.json()["missing"] == ["prices"]


# ── the find-care routes (application/serving/routers/find_care.py), over the default corpus ──

SILVER = "45334GA0020002"
MRI = {"service_id": "mri-lower-back", "plan_id": SILVER, "zip": "30308"}


def test_find_care_plans_run_by_tier_then_deductible_with_what_tells_them_apart(api):
    anthem = "Anthem Blue Cross and Blue Shield"
    assert api.get("/api/plans").json() == [
        {"plan_id": SILVER, "name": "Anthem Silver Blue Value HMO 4000", "carrier": anthem,
         "metal_level": "Silver", "needs_referral": False, "deductible": 4000.0,
         "tier": "Silver", "detail": "Blue Value HMO"},
        {"plan_id": "45334GA0020008", "name": "Anthem Bronze Blue Value HMO 5000", "carrier": anthem,
         "metal_level": "Expanded Bronze", "needs_referral": False, "deductible": 5000.0,
         "tier": "Bronze", "detail": "Blue Value HMO"},
        {"plan_id": "49046GA0700053", "name": "Anthem Bronze Pathway PCP Copay Choice HMO 6000", "carrier": anthem,
         "metal_level": "Expanded Bronze", "needs_referral": False, "deductible": 6000.0,
         "tier": "Bronze", "detail": "Pathway PCP Copay Choice HMO"}]


def test_find_care_services_carry_what_the_screen_says(api):
    body = api.get("/api/services").json()
    by_id = {s["service_id"]: s for s in body["services"]}
    assert [s["name"] for s in body["services"]] == sorted(s["name"] for s in body["services"])
    assert by_id["mri-lower-back"]["medicare_rate"] == 180.0 and by_id["mri-lower-back"]["medicare_facility_rate"] == 243.77
    assert by_id["mri-lower-back"]["family"] == "mri"
    assert by_id["mammogram-screening"]["preventive_note"] == "Usually $0 in network as preventive care."


def test_the_catalog_lists_families_in_their_order(api):
    body = api.get("/api/services").json()
    assert body["families"] == [
        {"family": "office_visits", "name": "Office visits",
         "specialties": [{"specialty": "primary_care", "name": "Primary care", "benefit": "primary_care"},
                         {"specialty": "heart", "name": "Heart", "benefit": "specialist"},
                         {"specialty": "skin", "name": "Skin", "benefit": "specialist"}],
         "measured": False},
        {"family": "mammograms", "name": "Mammograms", "specialties": [], "measured": True},
        {"family": "mri", "name": "MRI scans", "specialties": [], "measured": True},
        {"family": "stays", "name": "Hospital stays", "specialties": [], "measured": False}]


def test_find_care_lists_every_place_on_the_plan_cheapest_first(api):
    body = api.get("/api/find-care", params=MRI).json()
    # Fewer than 5 places within any radius, so the search takes the widest.
    assert body["radius_mi"] == 50 and body["origin"] == {"zip": "30308", "lat": 33.770959, "lon": -84.377736}
    assert body["cost_sharing"] == [{"benefit": "advanced_imaging", "label": "CT, PET and MRI scans", "copay": None,
                                     "coinsurance": 40.0, "after_deductible": True, "copay_per": None, "copay_limited": False,
                                     "parts": ["all_in", "scan", "reading", "facility"]}]
    assert [(p["place_id"], p["distance_mi"], p["max_rate"]) for p in body["results"]] == [
        ("1000000001", 0.4, 500.0), ("1000000005", 3.5, 500.0), ("1000000002", 23.2, 500.0),
        ("110079", 1.3, None)]  # the hospital's own fee isn't on file
    assert body["cheapest_place_id"] == "1000000001" and body["closest_place_id"] == "1000000001"
    emory = body["results"][1]
    assert emory["name"] == "EMORY MEDICAL CARE FOUNDATION" and emory["billers"] == ["EMORY MEDICAL CARE FOUNDATION"]
    assert [{k: v for k, v in p.items() if k != "quality"} for p in emory["providers"]] == [
        {"npi": 1000000005, "name": "PAT SMITH", "specialty": "Diagnostic Radiology Physician", "score": 87.5}]
    grady = body["results"][3]
    assert grady["kind"] == "hospital" and grady["kind_name"] == "Hospital"
    assert grady["stars"] == 3 and grady["phone"] == "(404) 555-0100"
    assert emory["kind_name"] == "Medical practice"
    assert grady["parts"] == [
        {"part": "facility", "label": "Hospital facility fee", "billing_class": "institutional", "max": None, "terms": None,
         "source": None, "missing_note": "Missing the hospital's fee", "missing_explain": "Hospitals bill their own fee."},
        {"part": "reading", "label": "Radiologist's reading", "billing_class": "professional", "max": 200.0, "terms": None,
         "source": "plan", "missing_note": None, "missing_explain": None}]
    assert grady["cash_price"] == 1450.0 and emory["cash_price"] is None
    assert emory["parts"] == [{"part": "all_in", "label": "The scan and its reading", "billing_class": "professional",
                               "max": 500.0, "terms": None, "source": "plan", "missing_note": None, "missing_explain": None}]


def test_find_care_serves_a_hospital_with_its_own_fee_and_no_reading_group(api):
    body = api.get("/api/find-care", params={**MRI, "service_id": "mammogram-screening",
                                             "plan_id": "49046GA0700053", "zip": "30720"})
    assert body.status_code == 200
    by_id = {p["place_id"]: p for p in body.json()["results"]}
    fee_only = by_id["110001"]
    assert fee_only["name"] == "HAMILTON MEDICAL CENTER" and fee_only["billers"] == [] and fee_only["providers"] == [] and fee_only["max_rate"] is None
    assert [(p["part"], p["max"], p["missing_note"], p["missing_explain"]) for p in fee_only["parts"]] == [
        ("facility", 165.0, None, None),
        ("reading", None, "Missing the reading", "Ask the hospital who will read yours.")]  # the hospital's wording
    assert fee_only["cash_price"] is None


def test_find_care_says_where_a_hospitals_fee_comes_from_and_what_it_charges_uninsured(api):
    body = api.get("/api/find-care", params={**MRI, "service_id": "mammogram-screening"}).json()
    grady = next(p for p in body["results"] if p["place_id"] == "110079")
    assert [(p["part"], p["max"], p["source"]) for p in grady["parts"]] == [
        ("facility", 210.0, "hospital"), ("reading", 35.0, "plan")]
    assert grady["cash_price"] == 320.0 and grady["max_rate"] == 245.0


def test_find_care_keeps_to_the_radius_and_sorts_by_distance(api):
    body = api.get("/api/find-care", params={**MRI, "radius_mi": 5, "sort": "distance"}).json()
    assert [p["place_id"] for p in body["results"]] == ["1000000001", "110079", "1000000005"]
    assert body["wider_radius_mi"] is None and body["closest_outside"] is None


def test_find_care_with_no_place_near_points_to_the_closest_outside(api):
    body = api.get("/api/find-care", params={**MRI, "zip": "30720", "radius_mi": 25}).json()
    assert body["results"] == [] and body["closest_outside"]["place_id"] == "1000000002"
    assert body["wider_radius_mi"] is None  # Hiram is beyond the widest radius from Dalton
    pathway = api.get("/api/find-care", params={**MRI, "plan_id": "49046GA0700053"}).json()
    assert pathway["results"] == [] and pathway["closest_outside"] is None


def test_find_care_charges_preventive_care_nothing(api):
    body = api.get("/api/find-care", params={**MRI, "service_id": "mammogram-screening"}).json()
    assert [(s["benefit"], s["copay"], s["coinsurance"], s["after_deductible"]) for s in body["cost_sharing"]] == [
        ("preventive", 0.0, None, False)]


def test_find_care_gives_a_service_billed_under_two_benefits_a_share_for_each(api):
    body = api.get("/api/find-care", params={**MRI, "service_id": "office-visit-established"}).json()
    assert [(s["benefit"], s["label"], s["copay"], s["parts"]) for s in body["cost_sharing"]] == [
        ("primary_care", "Primary care visit", 30.0, ["visit"]), ("specialist", "Specialist visit", 60.0, ["visit"])]
    assert [(p["place_id"], p["max_rate"]) for p in body["results"]] == [("1000000070", 95.0)]


def test_a_missing_part_says_what_is_missing_the_hospital_way_only_at_a_hospital(api):
    # The reading is missing at a hospital (its own wording) and the scan at a practice-like place has none.
    body = api.get("/api/find-care", params={**MRI, "plan_id": "49046GA0700053", "zip": "30720",
                                             "service_id": "mammogram-screening"}).json()
    reading = next(p for p in next(r for r in body["results"] if r["place_id"] == "110001")["parts"]
                   if p["part"] == "reading")
    assert reading["missing_explain"] == "Ask the hospital who will read yours."


def test_a_practices_missing_facility_part_says_the_practice_way_and_other_places_keep_theirs():
    bill = {"part": "facility", "label": "The facility's fee", "billing_class": "institutional",
            "missing_note": "Missing the facility's fee", "missing_explain": "Ask where.",
            "missing_explain_hospital": "Hospitals bill their own fee.",
            "missing_note_practice": "Plus the facility's own fee", "missing_explain_practice": "A hospital bills it."}
    def said(kind, **own):
        place = {"kind": kind, "parts": [{"part": "facility", "max": None, "terms": None}]}
        part = with_bill_text(place, [{**bill, **own}])["parts"][0]
        return part["missing_note"], part["missing_explain"]
    assert said("practice") == ("Plus the facility's own fee", "A hospital bills it.")
    assert said("hospital") == ("Missing the facility's fee", "Hospitals bill their own fee.")
    assert said("surgery_center") == ("Missing the facility's fee", "Ask where.")
    assert said("practice", missing_note_practice=None, missing_explain_practice=None) == (
        "Missing the facility's fee", "Ask where.")


@pytest.mark.parametrize("params, code", [
    ({**MRI, "service_id": "mri-elbow"}, "unknown_service"),
    ({**MRI, "plan_id": "58081GA0010001"}, "unknown_plan"),  # its network isn't priced
    ({**MRI, "zip": "99999"}, "unknown_zip"),
])
def test_find_care_says_what_it_doesnt_know(api, params, code):
    r = api.get("/api/find-care", params=params)
    assert r.status_code == 404 and r.json()["detail"]["code"] == code


def place(distance_mi, *maxes):
    return {"distance_mi": distance_mi, "parts": [{"part": "all_in", "max": m} for m in maxes]}


def test_a_search_with_no_radius_takes_the_tightest_with_five_places():
    near = [place(d, 100) for d in (1, 2, 3, 4, 8, 30)]
    assert radius_for(near, None) == 10
    assert radius_for(near[:3], None) == 50
    assert radius_for(near, 25) == 25


def test_a_search_with_no_full_price_points_to_the_nearest_one():
    places = [place(2, None), place(7, None), place(18, 300), place(60, 200)]
    assert wider_radius(places, places[:2]) == 25
    assert wider_radius(places, []) == 5
    assert wider_radius(places[:2], places[:2]) is None
    assert wider_radius([place(70, 100)], []) is None
    assert wider_radius(places, places[:3]) is None


def test_the_app_imports_nothing_from_ingestion_or_integration():
    r = subprocess.run([sys.executable, "-c",
                        "import sys, application.serving.main; "
                        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('ingestion', 'integration')))"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"


# ── asking which kind of doctor (office visits), over the default corpus's one practice ──

VISIT = {"service_id": "office-visit-established", "plan_id": SILVER, "zip": "30308"}


def test_find_care_without_a_specialty_lists_every_row_and_every_clinician(api):
    body = api.get("/api/find-care", params=VISIT).json()
    assert body["specialty"] is None
    (place,) = body["results"]
    assert [p["name"] for p in place["providers"]] == ["PAT HADDAD", "PAT OKAFOR", "PAT REYES"]
    assert [s["benefit"] for s in body["cost_sharing"]] == ["primary_care", "specialist"]


def test_find_care_for_a_specialty_keeps_its_rows_and_its_clinicians(api):
    for kind, who in (("primary_care", ["PAT OKAFOR"]), ("heart", ["PAT HADDAD"])):
        body = api.get("/api/find-care", params={**VISIT, "specialty": kind}).json()
        assert body["specialty"] == kind
        (place,) = body["results"]
        assert [p["name"] for p in place["providers"]] == who
        assert body["median_max_rate"] == place["max_rate"] == body["results"][0]["max_rate"]
        assert body["cheapest_place_id"] == body["closest_place_id"] == place["place_id"]


def test_find_care_for_a_specialty_shows_only_that_specialtys_share(api):
    shares = {kind: api.get("/api/find-care", params={**VISIT, "specialty": kind}).json()["cost_sharing"]
              for kind in ("primary_care", "heart")}
    assert [(s["benefit"], s["copay"]) for s in shares["primary_care"]] == [("primary_care", 30.0)]
    assert [(s["benefit"], s["copay"]) for s in shares["heart"]] == [("specialist", 60.0)]


def test_find_care_for_a_specialty_nobody_here_has_lists_no_place(api):
    body = api.get("/api/find-care", params={**VISIT, "specialty": "skin"}).json()
    assert body["results"] == [] and body["closest_outside"] is None and body["median_max_rate"] is None


@pytest.mark.parametrize("params, code", [
    ({**VISIT, "specialty": "astrology"}, "unknown_specialty"),
    ({**MRI, "specialty": "heart"}, "specialty_not_asked"),  # an MRI doesn't ask which kind of doctor
])
def test_find_care_with_a_specialty_that_does_not_fit_is_a_422_with_the_usual_problem(api, params, code):
    r = api.get("/api/find-care", params=params)
    assert r.status_code == 422 and r.json()["detail"]["code"] == code and r.json()["detail"]["message"]


def measures(rows):
    return [m["measure"] for m in rows]


def test_find_care_gives_each_place_and_clinician_only_the_measures_that_fit_the_service(api):
    body = api.get("/api/find-care", params=MRI).json()
    by_id = {p["place_id"]: p for p in body["results"]}
    assert measures(by_id["110079"]["quality"]) == ["OP-32"]  # the hospital's OP-39 is a mammogram measure
    assert by_id["110079"]["quality"][0] == {
        "measure": "OP-32", "name": "Hospital visits after a colonoscopy", "about": "Lower is better.",
        "shown": "13.9 per 1,000", "compared": "No different than the national rate", "period": "2022 to 2024",
        "source": "CMS hospital outpatient measures"}
    assert measures(by_id["1000000001"]["quality"]) == ["ASC-12"]
    assert by_id["1000000001"]["quality"][0]["source"] == "CMS surgery center measures"
    (smith,) = by_id["1000000005"]["providers"]
    assert smith["quality"] == [{
        "measure": "volume-colonoscopy", "name": "Colonoscopies done", "about": "More suggests practice.",
        "shown": "57 procedures", "compared": None, "period": None, "source": "CMS Doctors and Clinicians"}]
    assert by_id["1000000002"]["quality"] == []  # no measure for it


def test_find_care_for_a_service_a_measure_doesnt_fit_has_empty_quality(api):
    body = api.get("/api/find-care", params=VISIT).json()
    (place,) = body["results"]
    assert place["quality"] == [] and all(p["quality"] == [] for p in place["providers"])


def test_the_catalog_says_which_families_have_a_measure(api):
    body = api.get("/api/services").json()
    assert {f["family"]: f["measured"] for f in body["families"]} == {
        "office_visits": False, "mammograms": True, "mri": True, "stays": False}


# ── extra bills: what the plan usually pays for a bill the patient doesn't choose who sends ──

def test_find_care_gives_a_service_the_plans_usual_range_for_the_bills_it_brings(api):
    body = api.get("/api/find-care", params=MRI).json()
    assert body["extra_bills"] == [
        {"part": "anesthesia", "label": "Anesthesia", "note": "Ask the facility which group they use.",
         "low": None, "typical": None, "high": None},
        {"part": "pathology", "label": "Lab exam of removed tissue", "note": "The lab bills it.",
         "low": 32.0, "typical": 40.0, "high": 48.0}]
    # The range is not part of any place's most you'd pay.
    assert [p["max_rate"] for p in body["results"]][:1] == [500.0]


def test_find_care_extra_bills_follow_the_plans_network(api):
    body = api.get("/api/find-care", params={**MRI, "plan_id": "49046GA0700053"}).json()
    anesthesia, pathology = body["extra_bills"]
    assert (anesthesia["part"], anesthesia["note"], anesthesia["low"], anesthesia["typical"], anesthesia["high"]) == (
        "anesthesia", "The facility's group bills it.", 220.0, 300.0, 380.0)
    assert (pathology["note"], pathology["typical"]) == ("Ask your doctor which lab they use.", None)


PATHWAY_PLAN = "49046GA0700053"
STAY = {"service_id": "stay-vaginal-delivery", "zip": "30308"}


def test_find_care_a_stay_priced_by_the_day_has_terms_and_no_max(api):
    body = api.get("/api/find-care", params={**STAY, "plan_id": PATHWAY_PLAN}).json()
    (grady,) = body["results"]
    assert grady["place_id"] == "110079" and grady["max_rate"] is None
    assert [(p["part"], p["max"], p["terms"], p["source"], p["missing_note"]) for p in grady["parts"]] == [
        ("stay", None, "$2,472 a day", "plan", None)]
    assert body["empty_note"] is None
    assert [(e["part"], e["typical"]) for e in body["extra_bills"]] == [("anesthesia", None), ("doctor", 1800.0)]


def test_find_care_says_what_the_family_says_when_the_plan_has_no_place_for_a_stay(api):
    body = api.get("/api/find-care", params={**STAY, "plan_id": SILVER}).json()
    assert body["results"] == [] and body["closest_outside"] is None
    assert body["empty_note"] == "No hospital shows a price for a stay."
    doctor = body["extra_bills"][1]
    assert (doctor["part"], doctor["label"], doctor["typical"]) == ("doctor", "The doctor's fee", 1800.0)


def test_find_care_has_no_empty_note_when_a_place_exists_or_the_family_has_none(api):
    assert api.get("/api/find-care", params={**MRI}).json()["empty_note"] is None
    body = api.get("/api/find-care", params={**VISIT, "zip": "30308"}).json()
    assert body["empty_note"] is None


def test_find_care_extra_bills_are_empty_for_a_service_that_names_none(api):
    body = api.get("/api/find-care", params={"service_id": "mammogram-screening", "plan_id": SILVER, "zip": "30308"}).json()
    assert body["extra_bills"] == []
