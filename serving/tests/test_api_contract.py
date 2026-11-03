"""API contract tests — every route answered against the synthetic fixture in
conftest.py (`api` fixture). Hermetic: no live server, no data/ mount.

Checks response shape and the invariants a consumer relies on. Real-data
assertions live in test_golden.py.
"""
BLUE_VALUE = "GA Blue Value HIX Individual Network"


def test_health_and_trust_bar(api):
    body = api.get("/").json()
    assert body["status"] == "ok"
    assert body["total_prices"] > 0
    assert body["total_group_set_edges"] > 0
    # NPIs 1-5 plus Ng (1000000011, added for the #87 cost-sort test)
    assert body["priceable_npis"] == 6
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
    # price-row count per network — 18 Blue Value (the original 13, incl. the 3
    # out-of-scope noise rows: rate_summary counts "what exists", not the
    # scoped view; plus 4 for the #87 cost-sort / plausible-tier fixture —
    # Baker's 99204 and 99202 rates, Ng's 99204 rate, and Ng's unrelated $10
    # 12002 row, and the facility-only 70450 line) + 5 Open Access.
    body = api.get("/networks").json()
    counts = {r["network_name"]: r["n_rates"] for r in body}
    assert counts == {BLUE_VALUE: 18, "GA Blue Open Access POS Network": 5}
    # sorted by volume desc
    assert [r["n_rates"] for r in body] == sorted((r["n_rates"] for r in body), reverse=True)


def test_plans_resolves_blue_value(api):
    body = api.get("/plans").json()
    assert body
    for row in body:
        assert {"plan", "network_name", "available"} <= row.keys()
    bv = next((p for p in body if "blue value" in p["plan"].lower()), None)
    assert bv is not None
    assert bv["network_name"] == BLUE_VALUE
    assert bv["available"] is True


def test_plans_lists_every_curated_plan(api):
    # Step 8 — a second GA individual plan sits beside Blue Value in the picker.
    body = api.get("/plans").json()
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
    r = api.get("/plans/cost_sharing", params={"network_name": BLUE_VALUE})
    assert r.status_code == 200
    versions = r.json()
    assert versions and all(v["network_name"] == BLUE_VALUE for v in versions)
    for v in versions:
        assert v["deductible"] > 0 and v["oop_max"] >= v["deductible"]
        assert v["source"].startswith("https://sbc.anthem.com/")
        for kind in ("diagnostic", "imaging"):
            assert ("coinsurance" in v[kind]) != ("text" in v[kind])
    assert api.get("/plans/cost_sharing", params={"network_name": "Nope"}).json() == []
    assert api.get("/plans/cost_sharing").status_code == 422


def test_imaging_providers_carries_a_labelled_facility_estimate(api):
    r = api.get("/imaging/providers", params={"billing_code": "70450", "network_name": BLUE_VALUE})
    assert r.json()["facility_estimate"] == {"amount": 106.81, "source": "medicare_opps", "kind": "estimate"}
