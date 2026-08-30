"""API contract tests — every route answered against the synthetic fixture in
conftest.py (`api` fixture). Hermetic: no live server, no data/ mount.

Checks response shape and the invariants a consumer relies on (sorted orders,
min <= median <= max, the npi-without-code guard, evidence tiering, the curated
plan map). Coverage-basket / real-data assertions stay in test_coverage.py.
"""
BLUE_VALUE = "GA Blue Value HIX Individual Network"
CARDIOLOGIST = 1000000001
LCSW = 1000000003
HOSPITAL_ORG = 1000000005


def test_health_and_trust_bar(api):
    body = api.get("/").json()
    assert body["status"] == "ok"
    assert body["total_prices"] > 0
    assert body["total_group_set_edges"] > 0
    assert body["priceable_npis"] == 5
    assert set(body["networks"]) == {BLUE_VALUE, "GA Blue Open Access POS Network"}
    assert body["n_codes"] == 5
    assert body["as_of"] is None or len(body["as_of"]) == 10


def test_distribution_shape(api):
    r = api.get("/rates/distribution", params={"billing_code": "99213", "billing_code_type": "CPT"})
    assert r.status_code == 200
    body = r.json()
    assert body["billing_code"] == "99213"
    s = body["summary"]
    assert {"min", "max", "avg", "median", "provider_groups", "n_providers", "total_entries"} <= s.keys()
    assert s["min"] <= s["median"] <= s["max"]
    # total_entries counts price rows expanded to provider groups (>= the 5 raw rows)
    assert s["total_entries"] >= 5
    assert isinstance(body["distribution"], list) and body["distribution"]


def test_distribution_overview_from_summary(api):
    # No billing_code → network overview, served off summary/rate_hist.parquet
    # (never a `prices` scan). CPT-only; per-group counts aren't derivable here.
    r = api.get("/rates/distribution")
    assert r.status_code == 200
    body = r.json()
    assert body["billing_code"] == "ALL"
    s = body["summary"]
    assert {"min", "max", "avg", "median", "n_codes", "total_entries"} <= s.keys()
    assert s["provider_groups"] is None
    assert s["n_providers"] is None
    assert s["n_codes"] >= 1
    assert s["min"] <= s["median"] <= s["max"]
    assert s["total_entries"] >= 5
    assert isinstance(body["distribution"], list) and body["distribution"]
    assert all({"rate", "provider_groups"} <= b.keys() for b in body["distribution"])

    # a network scope is a subset of the all-networks total
    scoped = api.get("/rates/distribution", params={"network_name": BLUE_VALUE}).json()
    assert scoped["summary"]["total_entries"] <= s["total_entries"]


def test_distribution_rejects_npi_without_code(api):
    r = api.get("/rates/distribution", params={"npi": CARDIOLOGIST})
    assert r.status_code == 400


def test_distribution_specialty_scope_narrows(api):
    base = api.get("/rates/distribution",
                   params={"billing_code": "99213", "billing_code_type": "CPT"}).json()
    scoped = api.get("/rates/distribution",
                     params={"billing_code": "99213", "billing_code_type": "CPT",
                             "specialty": "Cardiovascular Disease"})
    assert scoped.status_code == 200
    assert scoped.json()["summary"]["provider_groups"] <= base["summary"]["provider_groups"]


def test_rates_providers_shape(api):
    r = api.get("/rates/providers", params={"billing_code": "99213", "limit": 10})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results
    for row in results:
        assert {"provider_group_id", "negotiated_rate", "network_name"} <= row.keys()


def test_rates_by_network_sorted_and_bounded(api):
    r = api.get("/rates/by_network", params={"billing_code": "99213", "billing_code_type": "CPT"})
    assert r.status_code == 200
    nets = r.json()["networks"]
    assert len(nets) == 2
    for n in nets:
        assert {"network_name", "median", "min", "max", "typical_low", "typical_high",
                "n_groups"} <= n.keys()
        assert n["min"] <= n["median"] <= n["max"]
        assert n["typical_low"] <= n["typical_high"]
    assert nets == sorted(nets, key=lambda x: x["median"])
    # Blue Value was priced cheaper in the fixture
    assert nets[0]["network_name"] == BLUE_VALUE


def test_quote_components_and_evidence(api):
    r = api.get("/rates/quote", params={"billing_code": "70450", "billing_code_type": "CPT",
                                        "npi": 1000000004})  # radiologist in a Blue Value group
    assert r.status_code == 200
    body = r.json()
    assert body["headline"]["rate"] <= body["headline"]["max_rate"]
    assert body["components"]
    mods = [c["modifier"] for c in body["components"]]
    if "" in mods:
        assert mods[0] == ""  # global component sorts first
    for c in body["components"]:
        assert {"modifier", "label", "settings"} <= c.keys()
        for s in c["settings"]:
            assert s["min_rate"] <= s["max_rate"]
            assert s["pos_label"]
    assert "medicare_utilization" in body  # key always present


def test_quote_medicare_billed_flag(api):
    # the cardiologist billed 93000 to Medicare in the fixture
    body = api.get("/rates/quote", params={"billing_code": "93000", "npi": CARDIOLOGIST}).json()
    mu = body["medicare_utilization"]
    assert mu is not None and mu["billed"] is True


def test_provider_menu_tiers(api):
    r = api.get(f"/providers/{CARDIOLOGIST}/procedures", params={"network_name": BLUE_VALUE})
    assert r.status_code == 200
    body = r.json()
    assert body["npi"] == CARDIOLOGIST
    assert body["count"] == len(body["results"])
    assert {"tier", "group_count"} <= body.keys()
    for row in body["results"]:
        assert {"billing_code", "min_rate", "median_rate", "max_rate", "n_rates"} <= row.keys()
        assert row["min_rate"] <= row["max_rate"]
        assert row["tier"] in ("billed", "typical", "group")
    codes = {row["billing_code"]: row["tier"] for row in body["results"]}
    assert codes.get("93000") == "billed"    # Tier 1 — billed to Medicare
    assert codes.get("70450") == "typical"   # Tier 2 — typical for Cardiovascular Disease
    # ?tier=all is a superset
    all_body = api.get(f"/providers/{CARDIOLOGIST}/procedures",
                       params={"network_name": BLUE_VALUE, "tier": "all", "limit": 500}).json()
    assert len(all_body["results"]) >= len(body["results"])


def test_provider_search_by_name(api):
    body = api.get("/providers/search", params={"q": "adams", "limit": 5}).json()
    assert body
    hit = next((p for p in body if p["npi"] == CARDIOLOGIST), None)
    assert hit and {"specialty", "entity_type", "has_rates"} <= hit.keys()
    assert hit["has_rates"] is True


def test_provider_search_by_specialty(api):
    body = api.get("/providers/search", params={"specialty": "cardio", "limit": 10}).json()
    assert body
    for row in body:
        assert row.get("specialty")
    rated = [x["has_rates"] for x in body]
    assert rated == sorted(rated, reverse=True)  # rated providers rank first


def test_specialties_endpoint(api):
    body = api.get("/specialties", params={"q": "cardio"}).json()
    assert body
    for row in body:
        assert {"specialty", "n_providers", "n_with_rates"} <= row.keys()
        assert row["n_providers"] >= row["n_with_rates"] > 0


def test_ga_providers_hospitals_only(api):
    body = api.get("/providers/ga", params={"q": "emory", "hospitals_only": "true", "limit": 5}).json()
    assert body["available"] is True
    assert body["results"]
    for row in body["results"]:
        assert row["is_hospital"] and row["city"]


def test_networks_endpoint(api):
    # served from the browse summary (make build-summary); n_rates is the exact
    # price-row count per network — 9 Blue Value + 5 Open Access in the fixture.
    body = api.get("/networks").json()
    counts = {r["network_name"]: r["n_rates"] for r in body}
    assert counts == {BLUE_VALUE: 9, "GA Blue Open Access POS Network": 5}
    # sorted by volume desc
    assert [r["n_rates"] for r in body] == sorted((r["n_rates"] for r in body), reverse=True)


def test_billing_codes_search(api):
    body = api.get("/billing_codes", params={"q": "colonoscopy"}).json()
    row = next((r for r in body if r["billing_code"] == "45378"), None)
    assert row is not None
    assert row["provider_groups"] >= 1  # from code_rollup
    by_code = api.get("/billing_codes", params={"q": "99213"}).json()
    assert any(r["billing_code"] == "99213" for r in by_code)


def test_procedure_categories(api):
    body = api.get("/procedure_categories").json()
    assert isinstance(body, list) and body
    for r in body:
        assert {"category", "subcategory", "n_codes", "provider_groups"} <= r.keys()
        assert r["n_codes"] >= 1
    # the colonoscopy code labelled "Procedure" shows up
    assert any(r["category"] == "Procedure" for r in body)


def test_browse_falls_back_without_summary(api, monkeypatch):
    """Endpoints still answer when the summary parquet is absent (the live
    prices ⨝ group_sets scan — VOL_CTE)."""
    from serving.routers import reference
    monkeypatch.setattr(reference, "have_summary", lambda: False)
    nets = api.get("/networks").json()
    assert {r["network_name"] for r in nets} == {BLUE_VALUE, "GA Blue Open Access POS Network"}
    assert all(r["n_rates"] > 0 for r in nets)
    codes = api.get("/billing_codes", params={"q": "99213"}).json()
    assert any(r["billing_code"] == "99213" for r in codes)


def test_plans_resolves_blue_value(api):
    body = api.get("/plans").json()
    assert body
    for row in body:
        assert {"plan", "network_name", "available"} <= row.keys()
    bv = next((p for p in body if "blue value" in p["plan"].lower()), None)
    assert bv is not None
    assert bv["network_name"] == BLUE_VALUE
    assert bv["available"] is True
