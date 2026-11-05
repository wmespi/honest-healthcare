"""API contract tests — every route answered against the synthetic fixture in
conftest.py (`api` fixture). Hermetic: no live server, no data/ mount.

Checks response shape and the invariants a consumer relies on. Real-data
assertions live in tests/live/test_golden.py.
"""
import pytest

from serving.routers.find_care import radius_for, wider_radius

BLUE_VALUE = "GA Blue Value HIX Individual Network"


def test_health_and_trust_bar(api):
    body = api.get("/").json()
    assert body["status"] == "ok"
    assert body["total_prices"] > 0
    assert body["total_group_set_edges"] > 0
    # NPIs 1-5, Ng (1000000011, added for the #87 cost-sort test), and the two
    # radiologists billing under their own NPIs (1000000012-13)
    assert body["priceable_npis"] == 8
    assert set(body["networks"]) == {BLUE_VALUE, "GA Blue Open Access POS Network"}
    # the original 5 plus 99204 + 12002 + 99202 (added for the #87 cost-sort
    # / plausible-tier fixture)
    assert body["n_codes"] == 8
    assert body["as_of"] is None or len(body["as_of"]) == 10
    # The fixture's raw data is a build input for every optional table
    # (conftest.py runs the real build.build.build() against it).
    assert body["reference_loaded"] == {
        "nppes": True, "nucc": True, "cms_utilization": True,
        "mpfs": True, "dac": True,
    }
    assert body["built_at"] is not None
    assert body["partial_build"] is False


def test_networks_endpoint(api):
    # served from the browse summary (make build-summary); n_rates is the exact
    # price-row count per network — 19 Blue Value (the original 13, incl. the 3
    # out-of-scope noise rows: rate_summary counts "what exists", not the
    # scoped view; plus 4 for the #87 cost-sort / plausible-tier fixture —
    # Baker's 99204 and 99202 rates, Ng's 99204 rate, and Ng's unrelated $10
    # 12002 row, the facility-only 70450 line, and the own-NPI radiologists'
    # 70450 rate) + 5 Open Access.
    body = api.get("/networks").json()
    counts = {r["network_name"]: r["n_rates"] for r in body}
    assert counts == {BLUE_VALUE: 19, "GA Blue Open Access POS Network": 5}
    # sorted by volume desc
    assert [r["n_rates"] for r in body] == sorted((r["n_rates"] for r in body), reverse=True)


def test_plans_resolves_blue_value(api):
    body = api.get("/imaging/plans").json()
    assert body
    for row in body:
        assert {"plan", "network_name", "available"} <= row.keys()
    bv = next((p for p in body if "blue value" in p["plan"].lower()), None)
    assert bv is not None
    assert bv["network_name"] == BLUE_VALUE
    assert bv["available"] is True


def test_plans_lists_every_curated_plan(api):
    # Step 8 — a second GA individual plan sits beside Blue Value in the picker.
    body = api.get("/imaging/plans").json()
    names = {p["plan"] for p in body}
    assert {"Blue Value HMO — Individual", "Pathway PCP Copay Choice — Individual"} <= names


def test_service_lines_endpoint(api):
    # #100 — the frontend reads the curated allowlists off the API instead of
    # keeping its own hand-synced copy.
    body = api.get("/service_lines").json()
    assert "pcp" in body
    assert "207Q00000X" in body["pcp"]["taxonomy_codes"]
    assert "99204" in body["pcp"]["billing_codes"]


def test_service_lines_imaging_menu(api):
    body = api.get("/service_lines").json()
    menu = body["imaging"]["menu"]
    assert any(m["code"] == "70450" and m["group"] and m["label"] for m in menu)
    assert body["imaging"]["billing_codes"] == [m["code"] for m in menu]
    assert "2085R0202X" in body["imaging"]["taxonomy_codes"]


def test_imaging_providers_splits_global_technical_professional(api):
    r = api.get("/imaging/providers", params={"billing_code": "70450", "network_name": BLUE_VALUE})
    assert r.status_code == 200
    body = r.json()
    rows = {p["practice_id"]: p for p in body["results"]}
    row = rows["1000000010"]
    assert row["basis"] == "global"
    assert row["typical_rate"] == 240.0
    assert row["global"]["typical"] == 240.0
    assert row["technical"]["typical"] == 175.0
    assert row["professional"]["typical"] == 70.0


def test_imaging_providers_ignores_facility_only_price_lines(api):
    r = api.get("/imaging/providers", params={"billing_code": "70450", "network_name": BLUE_VALUE})
    row = {p["practice_id"]: p for p in r.json()["results"]}["1000000010"]
    assert row["global"]["max"] == 240.0 and row["global"]["min"] == 240.0


def test_imaging_providers_city_is_the_practices_own(api):
    # The practice's one imaging doctor works in Marietta, but the practice is
    # registered in Atlanta. The row names the practice, so it shows the practice's city.
    for params in ({}, {"zip": "30309"}):
        r = api.get("/imaging/providers", params={"billing_code": "70450", "network_name": BLUE_VALUE, **params})
        row = {p["practice_id"]: p for p in r.json()["results"]}["1000000010"]
        assert row["city"] == "Atlanta"


def test_imaging_providers_names_a_doctors_billing_id_after_their_cms_group(api):
    # Diaz and Ito each bill under their own NPI and both bill Medicare with
    # Marietta Imaging Partners, so they are one practice, under the group's name.
    r = api.get("/imaging/providers", params={"billing_code": "70450", "network_name": BLUE_VALUE})
    rows = {p["practice_id"]: p for p in r.json()["results"]}
    assert rows["1000000004"]["practice_name"] == "Marietta Imaging Partners"
    assert rows["1000000004"]["city"] == "Marietta"
    assert "ga_indiv_names" not in rows["1000000004"]
    assert "1000000012" not in rows
    # Okafor has no CMS group, and a doctor's own name doesn't make a place.
    assert "1000000013" not in rows


def test_imaging_providers_drops_a_biller_with_no_georgia_record(api):
    # With no Georgia NPPES record there is no name or address: not a place to go.
    for params in ({}, {"zip": "30309"}):
        r = api.get("/imaging/providers", params={"billing_code": "70450", "network_name": BLUE_VALUE, **params})
        ids = {p["practice_id"] for p in r.json()["results"]}
        assert "1000000010" in ids and "1999999999" not in ids


def test_imaging_providers_distance_is_from_the_practices_own_address(api):
    # The practice's own address is about 3 miles from this ZIP. Its one imaging
    # doctor has no coordinates, so a distance can only have come from the practice.
    params = {"billing_code": "70450", "network_name": BLUE_VALUE, "zip": "30309"}
    row = {p["practice_id"]: p for p in api.get("/imaging/providers", params=params).json()["results"]}["1000000010"]
    assert 2.5 < row["distance_mi"] < 3.5
    assert any(p["practice_id"] == "1000000010" for p in api.get(
        "/imaging/providers", params={**params, "radius_mi": 5}).json()["results"])
    assert not any(p["practice_id"] == "1000000010" for p in api.get(
        "/imaging/providers", params={**params, "radius_mi": 1}).json()["results"])


def test_imaging_providers_distance_falls_back_to_the_practices_zip(api, monkeypatch):
    # Census couldn't place this practice, so the center of its own ZIP stands in,
    # about 7 miles away, rather than dropping it from a nearby search.
    from serving.routers import imaging
    monkeypatch.setattr(imaging, "PROVIDER_DIM_SRC",
                        f"(SELECT * REPLACE (NULL::DOUBLE AS lat, NULL::DOUBLE AS lon) FROM {imaging.PROVIDER_DIM_SRC})")
    params = {"billing_code": "70450", "network_name": BLUE_VALUE, "zip": "30309"}
    row = {p["practice_id"]: p for p in api.get("/imaging/providers", params=params).json()["results"]}["1000000010"]
    assert 6.5 < row["distance_mi"] < 7.5


def test_imaging_providers_answers_the_same_every_time(api):
    params = {"billing_code": "70450", "network_name": BLUE_VALUE}
    first = api.get("/imaging/providers", params=params).json()
    assert first["results"]
    assert all(api.get("/imaging/providers", params=params).json() == first for _ in range(5))


def test_imaging_providers_excludes_non_imaging_networks(api):
    r = api.get("/imaging/providers", params={"billing_code": "70450", "network_name": BLUE_VALUE})
    assert "1000000030" not in {p["practice_id"] for p in r.json()["results"]}


def test_imaging_providers_rejects_bad_requests(api):
    assert api.get("/imaging/providers", params={"billing_code": "99213", "network_name": BLUE_VALUE}).status_code == 400
    r = api.get("/imaging/providers", params={"billing_code": "70450"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "network_required"
    assert api.get("/imaging/providers", params={
        "billing_code": "70450", "network_name": BLUE_VALUE, "radius_mi": 10}).status_code == 400
    assert api.get("/imaging/providers", params={
        "billing_code": "70450", "network_name": BLUE_VALUE, "zip": "00000"}).status_code == 400


def test_plan_cost_sharing_lists_sourced_versions_for_a_network(api):
    r = api.get("/imaging/plans/cost_sharing", params={"network_name": BLUE_VALUE})
    assert r.status_code == 200
    versions = r.json()
    assert versions and all(v["network_name"] == BLUE_VALUE for v in versions)
    for v in versions:
        assert v["deductible"] > 0 and v["oop_max"] >= v["deductible"]
        assert v["source"].startswith("https://sbc.anthem.com/")
        for kind in ("diagnostic", "imaging"):
            assert ("coinsurance" in v[kind]) != ("text" in v[kind])
    assert api.get("/imaging/plans/cost_sharing", params={"network_name": "Nope"}).json() == []
    assert api.get("/imaging/plans/cost_sharing").status_code == 422


def test_imaging_providers_carries_a_labelled_facility_estimate(api):
    r = api.get("/imaging/providers", params={"billing_code": "70450", "network_name": BLUE_VALUE})
    assert r.json()["facility_estimate"] == {"amount": 106.81, "source": "medicare_opps", "kind": "estimate"}


# ── the find-care routes (serving/routers/find_care.py), over the default corpus ──

SILVER = "45334GA0020002"
MRI = {"service_id": "mri-lower-back", "plan_id": SILVER, "zip": "30308"}


def test_find_care_plans_are_georgias_plans_with_their_deductibles(api):
    assert api.get("/plans").json() == [
        {"plan_id": "45334GA0020008", "name": "Anthem Bronze Blue Value HMO 5000",
         "carrier": "Anthem Blue Cross and Blue Shield", "needs_referral": False, "deductible": 5000.0},
        {"plan_id": "49046GA0700053", "name": "Anthem Bronze Pathway PCP Copay Choice HMO 6000",
         "carrier": "Anthem Blue Cross and Blue Shield", "needs_referral": False, "deductible": 6000.0},
        {"plan_id": SILVER, "name": "Anthem Silver Blue Value HMO 4000",
         "carrier": "Anthem Blue Cross and Blue Shield", "needs_referral": False, "deductible": 4000.0}]


def test_find_care_services_carry_what_the_screen_says(api):
    body = api.get("/services").json()
    assert [s["service_id"] for s in body] == ["mri-lower-back", "mammogram-screening"]
    assert body[0]["medicare_rate"] == 180.0 and body[0]["medicare_facility_rate"] == 243.77
    assert body[1]["preventive_note"] == "Usually $0 in network as preventive care."


def test_find_care_lists_every_place_on_the_plan_cheapest_first(api):
    body = api.get("/find-care", params=MRI).json()
    # Fewer than 5 places within any radius, so the search takes the widest.
    assert body["radius_mi"] == 50 and body["origin"] == {"zip": "30308", "lat": 33.770959, "lon": -84.377736}
    assert body["cost_sharing"] == {"copay": None, "coinsurance": 40.0, "after_deductible": True}
    assert [(p["place_id"], p["distance_mi"], p["max_rate"]) for p in body["results"]] == [
        ("1000000001", 0.4, 500.0), ("1000000005", 3.5, 500.0), ("1000000002", 23.2, 500.0),
        ("110079", 1.3, None)]  # the hospital's own fee isn't on file
    assert body["cheapest_place_id"] == "1000000001" and body["closest_place_id"] == "1000000001"
    emory = body["results"][1]
    assert emory["name"] == "EMORY MEDICAL CARE FOUNDATION" and emory["billers"] == ["1000000005"]
    assert emory["providers"] == [{"npi": 1000000005, "name": "PAT SMITH",
                                   "specialty": "Diagnostic Radiology Physician", "score": 87.5}]
    grady = body["results"][3]
    assert grady["kind"] == "hospital" and grady["stars"] == 3 and grady["phone"] == "(404) 555-0100"
    assert grady["parts"] == [{"part": "facility", "max": None}, {"part": "reading", "max": 200.0}]


def test_find_care_keeps_to_the_radius_and_sorts_by_distance(api):
    body = api.get("/find-care", params={**MRI, "radius_mi": 5, "sort": "distance"}).json()
    assert [p["place_id"] for p in body["results"]] == ["1000000001", "110079", "1000000005"]
    assert body["wider_radius_mi"] is None and body["closest_outside"] is None


def test_find_care_with_no_place_near_points_to_the_closest_outside(api):
    body = api.get("/find-care", params={**MRI, "zip": "30720", "radius_mi": 25}).json()
    assert body["results"] == [] and body["closest_outside"]["place_id"] == "1000000002"
    assert body["wider_radius_mi"] is None  # Hiram is beyond the widest radius from Dalton
    pathway = api.get("/find-care", params={**MRI, "plan_id": "49046GA0700053"}).json()
    assert pathway["results"] == [] and pathway["closest_outside"] is None


def test_find_care_charges_preventive_care_nothing(api):
    body = api.get("/find-care", params={**MRI, "service_id": "mammogram-screening"}).json()
    assert body["cost_sharing"] == {"copay": 0.0, "coinsurance": None, "after_deductible": False}


@pytest.mark.parametrize("params, code", [
    ({**MRI, "service_id": "mri-elbow"}, "unknown_service"),
    ({**MRI, "plan_id": "58081GA0010001"}, "unknown_plan"),  # its network isn't priced
    ({**MRI, "zip": "99999"}, "unknown_zip"),
])
def test_find_care_says_what_it_doesnt_know(api, params, code):
    r = api.get("/find-care", params=params)
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
