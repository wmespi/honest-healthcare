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

from application.serving.routers.find_care import radius_for, wider_radius

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def test_health_says_when_the_build_ran_and_what_it_holds(api):
    body = api.get("/api/health").json()
    assert body["status"] == "ok" and body["built_at"] is not None
    current = {"prices_status": "current", "prices_as_of": "2026-10-31", "prices_reason": None}
    assert body["networks"] == [
        {"network_id": "45334-GAN001", "name": "Blue Value Individual Network HMO", **current},
        {"network_id": "49046-GAN008", "name": "Pathway PCP Copay Choice", **current}]
    assert body["rows"] == {"plans": 3, "services": 2, "places": 5, "prices": 7, "providers": 1}


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
    assert [s["service_id"] for s in body] == ["mri-lower-back", "mammogram-screening"]
    assert body[0]["medicare_rate"] == 180.0 and body[0]["medicare_facility_rate"] == 243.77
    assert body[1]["preventive_note"] == "Usually $0 in network as preventive care."


def test_find_care_lists_every_place_on_the_plan_cheapest_first(api):
    body = api.get("/api/find-care", params=MRI).json()
    # Fewer than 5 places within any radius, so the search takes the widest.
    assert body["radius_mi"] == 50 and body["origin"] == {"zip": "30308", "lat": 33.770959, "lon": -84.377736}
    assert body["cost_sharing"] == {"copay": None, "coinsurance": 40.0, "after_deductible": True}
    assert [(p["place_id"], p["distance_mi"], p["max_rate"]) for p in body["results"]] == [
        ("1000000001", 0.4, 500.0), ("1000000005", 3.5, 500.0), ("1000000002", 23.2, 500.0),
        ("110079", 1.3, None)]  # the hospital's own fee isn't on file
    assert body["cheapest_place_id"] == "1000000001" and body["closest_place_id"] == "1000000001"
    emory = body["results"][1]
    assert emory["name"] == "EMORY MEDICAL CARE FOUNDATION" and emory["billers"] == ["EMORY MEDICAL CARE FOUNDATION"]
    assert emory["providers"] == [{"npi": 1000000005, "name": "PAT SMITH",
                                   "specialty": "Diagnostic Radiology Physician", "score": 87.5}]
    grady = body["results"][3]
    assert grady["kind"] == "hospital" and grady["stars"] == 3 and grady["phone"] == "(404) 555-0100"
    assert grady["parts"] == [{"part": "facility", "max": None, "source": None},
                              {"part": "reading", "max": 200.0, "source": "plan"}]
    assert grady["cash_price"] == 1450.0 and emory["cash_price"] is None
    assert emory["parts"] == [{"part": "all_in", "max": 500.0, "source": "plan"}]


def test_find_care_serves_a_hospital_with_its_own_fee_and_no_reading_group(api):
    body = api.get("/api/find-care", params={**MRI, "service_id": "mammogram-screening",
                                             "plan_id": "49046GA0700053", "zip": "30720"})
    assert body.status_code == 200
    by_id = {p["place_id"]: p for p in body.json()["results"]}
    fee_only = by_id["110001"]
    assert fee_only["name"] == "HAMILTON MEDICAL CENTER" and fee_only["billers"] == [] and fee_only["providers"] == [] and fee_only["max_rate"] is None
    assert fee_only["parts"] == [{"part": "facility", "max": 165.0, "source": "plan"},
                                 {"part": "reading", "max": None, "source": None}]
    assert fee_only["cash_price"] is None


def test_find_care_says_where_a_hospitals_fee_comes_from_and_what_it_charges_uninsured(api):
    body = api.get("/api/find-care", params={**MRI, "service_id": "mammogram-screening"}).json()
    grady = next(p for p in body["results"] if p["place_id"] == "110079")
    assert grady["parts"] == [{"part": "facility", "max": 210.0, "source": "hospital"},
                              {"part": "reading", "max": 35.0, "source": "plan"}]
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
    assert body["cost_sharing"] == {"copay": 0.0, "coinsurance": None, "after_deductible": False}


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
