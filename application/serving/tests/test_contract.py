"""The find-care contract's own rules (application/serving/contract.py), on the three kinds of
row the mock shows: a complete price, a hospital missing its fee, and a group
whose doctors only read scans done elsewhere."""
from typing import Annotated

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import ValidationError

from application.serving.contract import FindCare, FindCareQuery, Place, Plan, Service


def part(name, hi=None):
    return {"part": name, "max": hi}


def place(place_id="1000000010", kind="imaging_center", parts=None, stars=None, distance_mi=2.4, several_rates=False):
    return {
        "place_id": place_id, "name": "Dogwood Imaging", "kind": kind,
        "address": "215 E Ponce de Leon Ave, Decatur, GA 30030", "city": "Decatur",
        "lat": 33.7752, "lon": -84.2935, "phone": None, "stars": stars, "distance_mi": distance_mi,
        "parts": parts if parts is not None else [part("scan", 340), part("reading", 70)],
        "several_rates": several_rates,
        "billers": ["Dogwood Imaging"],
        "providers": [{"npi": 1000000004, "name": "Frank Diaz", "specialty": "Diagnostic Radiology", "score": 91.0}],
    }


COMPLETE = place(several_rates=True)
ALL_IN = place("1000000030", parts=[part("all_in", 390)], distance_mi=7.0)
HOSPITAL = place("110010", "hospital", [part("reading", 80), part("facility")], stars=4, distance_mi=0.8)
READS_ONLY = place("1000000020", "practice", [part("scan"), part("reading", 55)], distance_mi=5.1)


def search(results, **extra):
    return {
        "service_id": "mri-brain", "plan_id": "blue-value",
        "origin": {"zip": "30322", "lat": 33.79, "lon": -84.32}, "radius_mi": 10, "sort": "price",
        "cost_sharing": {"copay": 300.0, "coinsurance": 50.0, "after_deductible": True},
        "results": results, "wider_radius_mi": None, "closest_outside": None, **extra,
    }


def test_the_most_youd_pay_is_the_parts_added_up():
    assert Place.model_validate(COMPLETE).model_dump()["max_rate"] == 410
    for missing in (HOSPITAL, READS_ONLY):
        assert Place.model_validate(missing).model_dump()["max_rate"] is None


def test_results_sort_and_summarize_themselves():
    body = FindCare.model_validate(search([HOSPITAL, READS_ONLY, ALL_IN, COMPLETE])).model_dump()
    assert [p["place_id"] for p in body["results"]] == ["1000000030", "1000000010", "110010", "1000000020"]
    assert body["cheapest_place_id"] == "1000000030"
    assert body["closest_place_id"] == "110010"
    assert body["median_max_rate"] == 400
    by_distance = FindCare.model_validate(search([COMPLETE, READS_ONLY, HOSPITAL], sort="distance")).model_dump()
    assert [p["place_id"] for p in by_distance["results"]] == ["110010", "1000000010", "1000000020"]


def test_searches_with_no_full_price_or_no_results_fit():
    body = FindCare.model_validate(search([HOSPITAL], wider_radius_mi=25)).model_dump()
    assert body["cheapest_place_id"] is None and body["median_max_rate"] is None
    assert body["closest_place_id"] == "110010"
    body = FindCare.model_validate(search([], wider_radius_mi=50, closest_outside=COMPLETE)).model_dump()
    assert body["closest_place_id"] is None and body["closest_outside"]["max_rate"] == 410
    FindCare.model_validate(search([]))


@pytest.mark.parametrize("row, why", [
    (place(stars=4), "only a hospital has stars"),
    (place(parts=[]), "every place has a bill"),
    ({**COMPLETE, "billers": []}, "someone bills there"),
])
def test_a_place_breaking_a_rule_is_rejected(row, why):
    with pytest.raises(ValidationError):
        Place.model_validate(row)


@pytest.mark.parametrize("extra, why", [
    ({"wider_radius_mi": 25}, "widening is only for a search with no price"),
    ({"closest_outside": READS_ONLY}, "the closest outside is only for a search with no results"),
    ({"radius_mi": 15}, "a radius is one the screen offers"),
    ({"cost_sharing": {"copay": None, "coinsurance": None, "after_deductible": False}},
     "the plan charges a copay, a coinsurance or both"),
    ({"cost_sharing": {"copay": None, "coinsurance": 140.0, "after_deductible": True}},
     "a coinsurance is a percent"),
])
def test_a_search_breaking_a_rule_is_rejected(extra, why):
    with pytest.raises(ValidationError):
        FindCare.model_validate(search([COMPLETE, HOSPITAL], **extra))


def test_fastapi_publishes_the_contract():
    app = FastAPI()

    @app.get("/plans", response_model=list[Plan])
    def plans():
        return [{"plan_id": "blue-value", "name": "Blue Value HMO", "carrier": "Anthem", "needs_referral": True,
                 "deductible": 4000.0}]

    @app.get("/services", response_model=list[Service])
    def services():
        return [{"service_id": "mammogram", "name": "Screening mammogram", "keywords": "breast",
                 "about": "A low-dose X-ray.", "could_change": [], "asks": [],
                 "preventive_note": "Usually $0 in network.", "medicare_rate": 98.1,
                 "medicare_facility_rate": None}]

    @app.get("/find-care", response_model=FindCare)
    def find_care(q: Annotated[FindCareQuery, Query()]):
        return FindCare.model_validate(search([COMPLETE], radius_mi=q.radius_mi or 10, sort=q.sort))

    client = TestClient(app)
    schema = client.get("/openapi.json").json()
    params = {p["name"] for p in schema["paths"]["/find-care"]["get"]["parameters"]}
    assert params == {"service_id", "plan_id", "zip", "radius_mi", "sort"}
    place_out = next(v for k, v in schema["components"]["schemas"].items() if k.startswith("Place"))
    assert {"max_rate", "several_rates"} <= set(place_out["properties"])
    assert client.get("/plans").status_code == 200 and client.get("/services").status_code == 200
    body = client.get("/find-care", params={"service_id": "mri-brain", "plan_id": "blue-value", "zip": "30322"}).json()
    assert body["sort"] == "price" and body["cheapest_place_id"] == "1000000010"
    assert body["results"][0]["max_rate"] == 410
    bad = client.get("/find-care", params={"service_id": "mri-brain", "plan_id": "blue-value", "zip": "3032"})
    assert bad.status_code == 422
    near = {"service_id": "mri-brain", "plan_id": "blue-value", "zip": "30322"}
    assert client.get("/find-care", params={**near, "radius_mi": 25}).json()["radius_mi"] == 25
    assert client.get("/find-care", params={**near, "radius_mi": 15}).status_code == 422
