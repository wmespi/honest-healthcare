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


def test_the_build_holds(client):
    root = client.get("/health").json()
    assert [(n["network_id"], n["prices_status"]) for n in root["networks"]] == [
        (BLUE_VALUE, "current"), ("49046-GAN008", "current")]
    assert root["rows"] == {"plans": 11, "services": 26, "places": 992, "prices": 6313, "providers": 2090}


def test_a_brain_mri_near_emory_on_silver_4000(client):
    body = find_care(client, "mri-brain")
    assert body["radius_mi"] == 5 and len(body["results"]) == 9
    clinic = next(p for p in body["results"] if p["place_id"] == "1396798229")
    assert clinic["name"] == "THE EMORY CLINIC INC"
    assert clinic["parts"] == [{"part": "all_in", "max": 180.49}]
    assert body["median_max_rate"] == 180.49
    assert body["cost_sharing"] == {"copay": 300.0, "coinsurance": 50.0, "after_deductible": True}


def test_a_hospital_shows_its_readers_and_no_fee_of_its_own(client):
    grady = next(p for p in find_care(client, "mri-brain")["results"] if p["place_id"] == "110079")
    assert grady["parts"] == [{"part": "facility", "max": None}, {"part": "reading", "max": 66.48}]
    assert grady["max_rate"] is None
    assert grady["billers"] == ["EMORY MEDICAL CARE FOUNDATION INC"]


def test_a_brain_mri_carries_medicares_rates(client):
    brain = next(s for s in client.get("/services").json() if s["service_id"] == "mri-brain")
    assert brain["medicare_rate"] == 185.25 and brain["medicare_facility_rate"] == 243.77


def test_every_service_has_a_price_on_blue_value_within_50_miles(client):
    for service in client.get("/services").json():
        body = find_care(client, service["service_id"], radius_mi=50)
        assert body["results"], service["service_id"]


def test_a_search_needs_a_plan(client):
    assert client.get("/find-care", params={"service_id": "mri-brain", "zip": "30322"}).status_code == 422
