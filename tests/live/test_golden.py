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
        (BLUE_VALUE, "current"), ("49046-GAN008", "current")]
    assert root["rows"] == {"plans": 11, "services": 168, "families": 11, "places": 6073, "prices": 62784,
                            "providers": 12727}


def test_the_families_are_in_the_screens_order(client):
    catalog = client.get("/services").json()
    assert [f["family"] for f in catalog["families"]] == [
        "office_visits", "preventive_visits", "urgent_care", "mental_health", "therapy", "lab_tests", "xrays",
        "mammograms", "ultrasound", "ct", "mri"]
    assert {s["family"] for s in catalog["services"]} == {f["family"] for f in catalog["families"]}


def test_a_brain_mri_near_emory_on_silver_4000(client):
    body = find_care(client, "mri-brain")
    assert body["radius_mi"] == 5 and len(body["results"]) == 9
    clinic = next(p for p in body["results"] if p["place_id"] == "1396798229")
    assert clinic["name"] == "THE EMORY CLINIC INC"
    assert rates(clinic) == [("all_in", 180.49, "plan")]
    assert clinic["parts"][0]["label"] == "The scan and its reading" and clinic["kind_name"] == "Medical practice"
    assert body["median_max_rate"] == 180.49
    assert body["cost_sharing"] == [{
        "benefit": "advanced_imaging", "label": "CT, PET and MRI scans", "copay": 300.0, "coinsurance": 50.0,
        "after_deductible": True, "parts": ["all_in", "scan", "reading", "facility"]}]


def test_an_office_visit_near_emory_shows_one_bill_and_both_shares(client):
    body = find_care(client, "visit-return-20")
    clinic = next(p for p in body["results"] if p["place_id"] == "1396798229")
    assert rates(clinic) == [("visit", 90.5, "plan")]
    assert all(p["kind"] != "hospital" for p in body["results"])
    assert [(s["benefit"], s["parts"]) for s in body["cost_sharing"]] == [
        ("primary_care", ["visit"]), ("specialist", ["visit"])]


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
    assert rates(grady) == [("facility", 1171.15, "plan"), ("reading", 116.13, "plan")]
    assert grady["max_rate"] == 1287.28


def test_a_hospital_the_plans_file_leaves_out_shows_the_fee_in_its_own_price_list(client):
    emory = next(p for p in find_care(client, "xray-chest", plan_id=GOLD_PATHWAY)["results"] if p["place_id"] == "110010")
    assert emory["name"] == "EMORY UNIVERSITY HOSPITAL"
    assert rates(emory) == [("facility", 34.58, "hospital"), ("reading", None, None)]
    assert emory["max_rate"] is None and emory["billers"] == [] and emory["cash_price"] == 289.0


def test_a_brain_mri_carries_medicares_rates(client):
    brain = next(s for s in client.get("/services").json()["services"] if s["service_id"] == "mri-brain")
    assert brain["medicare_rate"] == 185.25 and brain["medicare_facility_rate"] == 243.77


def test_every_service_has_a_price_on_pathway_within_50_miles(client):
    assert unpriced(client, GOLD_PATHWAY) == []


def test_blue_value_prices_all_but_five_services_within_50_miles(client):
    # Its file has a rate for each, but no doctor Medicare shows billing it near Emory.
    assert unpriced(client, SILVER) == [
        "checkup-return-18-39", "counseling-family-with", "counseling-family-without", "counseling-group",
        "therapy-iontophoresis"]


def test_a_search_needs_a_plan(client):
    assert client.get("/find-care", params={"service_id": "mri-brain", "zip": "30322"}).status_code == 422
