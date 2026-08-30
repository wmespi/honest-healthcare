"""
Backend contract + coverage tests.

Contract tests (must pass): endpoints return 200 with the expected shape.
Coverage tests (xfail — monitoring signal, not a red build): the primary-use-case
basket of procedure codes resolves to rates.

Runs against the live API. Point at it with API_URL (default http://localhost:8000).
Inside the serving container use http://localhost:8000.
"""
import os

import httpx
import pytest

API = os.getenv("API_URL", "http://localhost:8000")

# A small, stable subset of the coverage_probe basket.
CORE_CODES = ["99213", "99214", "80053", "70450", "45378", "93000", "97110", "90837"]
KNOWN_GAP_CODES = ["00810"]  # anesthesia — no rates in the baseline 8 files


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=API, timeout=120) as c:
        yield c


def test_health(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body.get("total_prices", 0) > 0
    assert body.get("total_group_set_edges", 0) > 0
    # trust-bar context (issue #32)
    assert body["priceable_npis"] > 0
    assert isinstance(body["networks"], list) and body["networks"]
    assert body["n_codes"] > 0
    assert body["as_of"] is None or len(body["as_of"]) == 10  # YYYY-MM-DD


BLUE_VALUE = "GA Blue Value HIX Individual Network"


def test_distribution_shape(client):
    # code without a network → served off rate_hist (per-group counts null)
    r = client.get("/rates/distribution", params={"billing_code": "99213", "billing_code_type": "CPT"})
    assert r.status_code == 200
    body = r.json()
    assert body["billing_code"] == "99213"
    for k in ("min", "max", "avg", "median", "provider_groups", "n_providers", "total_entries"):
        assert k in body["summary"]
    assert isinstance(body["distribution"], list) and body["distribution"]


def test_providers_shape(client):
    r = client.get("/rates/providers", params={"billing_code": "99213"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "network_required"
    r = client.get("/rates/providers",
                   params={"billing_code": "99213", "network_name": BLUE_VALUE, "limit": 5})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results
    for row in results:
        assert {"practice_id", "negotiated_rate", "network_name", "npi_count"} <= row.keys()
        assert row["min_rate"] >= 1          # sentinel placeholders excluded


def test_provider_search_specialty(client):
    r = client.get("/providers/search", params={"q": "emory", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    if body:
        assert {"specialty", "entity_type"} <= body[0].keys()
    # specialty-only filter works without a text query
    r2 = client.get("/providers/search", params={"specialty": "cardio", "limit": 5})
    assert r2.status_code == 200
    for row in r2.json():
        assert row.get("specialty")
    # rated individuals rank ahead of no-rate orgs
    rated = [x["has_rates"] for x in r2.json()]
    assert rated == sorted(rated, reverse=True)


def test_specialties_endpoint(client):
    r = client.get("/specialties", params={"q": "cardio"})
    assert r.status_code == 200
    body = r.json()
    for row in body:
        assert {"specialty", "n_providers", "n_with_rates"} <= row.keys()
        assert row["n_with_rates"] > 0
        assert row["n_providers"] >= row["n_with_rates"]
    if body:
        assert any("cardio" in row["specialty"].lower() for row in body)


def test_rate_quote_provider_card(client, npi_with_rates):
    menu = client.get(f"/providers/{npi_with_rates}/procedures",
                      params={"network_name": BLUE_VALUE, "tier": "all"}).json()
    assert "provider" in menu
    code = next((m["billing_code"] for m in menu["results"] if m["billing_code_type"] == "CPT"), None)
    if code is None:
        pytest.skip("this NPI has no Blue Value CPT rate")
    r = client.get("/rates/quote", params={"billing_code": code, "npi": npi_with_rates,
                                           "network_name": BLUE_VALUE})
    assert r.status_code == 200
    assert "provider" in r.json()


def test_ga_providers_endpoint(client):
    r = client.get("/providers/ga", params={"q": "hospital", "hospitals_only": "true", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert "available" in body
    if body["available"]:
        assert all(row["is_hospital"] for row in body["results"])
        assert all(row["city"] for row in body["results"])


def test_rates_providers_nppes_annotation(client):
    r = client.get("/rates/providers",
                   params={"billing_code": "99213", "network_name": BLUE_VALUE, "limit": 3})
    assert r.status_code == 200
    body = r.json()
    if body.get("nppes_ga"):
        for row in body["results"]:
            assert "ga_hospital_npis" in row and "ga_org_names" in row


@pytest.fixture(scope="session")
def npi_with_rates(client):
    """An NPI with a Blue Value rate — the quote/menu views now need a network."""
    for q in ("emory", "piedmont", "northside", "wellstar", "kaiser"):
        r = client.get("/providers/search", params={"q": q, "limit": 30})
        for p in (r.json() if r.status_code == 200 else []):
            if not p.get("has_rates"):
                continue
            menu = client.get(f"/providers/{p['npi']}/procedures",
                              params={"network_name": BLUE_VALUE, "tier": "all"})
            if menu.status_code == 200 and menu.json().get("results"):
                return p["npi"]
    pytest.skip("no NPI with Blue Value rates found via provider search")


def test_provider_menu_shape(client, npi_with_rates):
    r = client.get(f"/providers/{npi_with_rates}/procedures",
                   params={"network_name": "GA Blue Value HIX Individual Network"})
    assert r.status_code == 200
    body = r.json()
    assert body["npi"] == npi_with_rates
    assert body["count"] == len(body["results"])
    for row in body["results"][:5]:
        assert {"billing_code", "min_rate", "median_rate", "max_rate", "n_rates"} <= row.keys()
        assert row["min_rate"] <= row["max_rate"]
    # tiering (issue #14): every row tagged; group_count present; ?tier=all is a superset
    assert {"tier", "group_count"} <= body.keys()
    for row in body["results"]:
        assert row["tier"] in ("billed", "typical", "group")
    all_body = client.get(f"/providers/{npi_with_rates}/procedures",
                          params={"network_name": "GA Blue Value HIX Individual Network",
                                  "tier": "all", "limit": 2000}).json()
    assert all_body["tier"] == "all"
    assert len(all_body["results"]) >= len(body["results"])


def test_rate_quote_shape(client, npi_with_rates):
    menu = client.get(f"/providers/{npi_with_rates}/procedures",
                      params={"network_name": BLUE_VALUE, "tier": "all"}).json()["results"]
    code = next((m["billing_code"] for m in menu if m["billing_code_type"] == "CPT"), None)
    if code is None:
        pytest.skip("this NPI has no Blue Value CPT rate")
    r = client.get("/rates/quote", params={
        "billing_code": code, "billing_code_type": "CPT", "npi": npi_with_rates,
        "network_name": BLUE_VALUE})
    assert r.status_code == 200
    body = r.json()
    assert body["headline"]["rate"] <= body["headline"]["max_rate"]
    assert body["components"]
    for c in body["components"]:
        assert {"modifier", "label", "settings"} <= c.keys()
        for s in c["settings"]:
            assert s["min_rate"] <= s["max_rate"]
            assert s["pos_label"]
    # global component (if present) sorts first
    mods = [c["modifier"] for c in body["components"]]
    if "" in mods:
        assert mods[0] == ""
    # CMS Medicare evidence (issue #14): key always present; null until
    # `make cms-utilization` has run, else {"billed": bool, ...}.
    assert "medicare_utilization" in body
    mu = body["medicare_utilization"]
    assert mu is None or "billed" in mu


def test_distribution_rejects_npi_without_code(client, npi_with_rates):
    r = client.get("/rates/distribution", params={"npi": npi_with_rates})
    assert r.status_code == 400


def test_specialty_scope_filter(client):
    """A specialty filter narrows to groups containing a provider of that
    specialty (issue #31 rework). Needs the live path → a network_name."""
    base = client.get("/rates/distribution",
                      params={"billing_code": "99213", "billing_code_type": "CPT",
                              "network_name": BLUE_VALUE}).json()
    scoped = client.get("/rates/distribution",
                        params={"billing_code": "99213", "billing_code_type": "CPT",
                                "network_name": BLUE_VALUE,
                                "specialty": "Cardiovascular Disease"})
    assert scoped.status_code == 200
    assert scoped.json()["summary"]["provider_groups"] <= base["summary"]["provider_groups"]
    # /rates/providers honours it too
    rp = client.get("/rates/providers",
                    params={"billing_code": "99213", "network_name": BLUE_VALUE,
                            "specialty": "Cardiovascular Disease", "limit": 5})
    assert rp.status_code == 200


def test_rates_by_network(client):
    r = client.get("/rates/by_network", params={"billing_code": "99213", "billing_code_type": "CPT"})
    assert r.status_code == 200
    nets = r.json()["networks"]
    assert nets
    for n in nets:
        assert {"network_name", "median", "min", "max", "typical_low", "typical_high",
                "spread", "n_groups", "n_providers"} <= n.keys()
        assert n["min"] <= n["median"] <= n["max"]
        assert n["typical_low"] <= n["typical_high"]
        # n_providers = distinct NPIs; n_groups = distinct *file-local* group
        # instances. Neither bounds the other at corpus scale — one practice
        # recurs as a group across many files (inflates n_groups), and rollup
        # groups name few NPIs (deflates n_providers). See docs/known-gaps.md
        # "code_rollup.n_provider_groups" / GH #48.
        assert n["n_groups"] >= 1
        assert n["n_providers"] is None or n["n_providers"] >= 0
    # sorted cheapest median first
    assert nets == sorted(nets, key=lambda x: x["median"])


def test_networks_endpoint(client):
    r = client.get("/networks")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    for row in body:
        assert {"network_name", "n_rates"} <= row.keys()


def test_plans_endpoint(client):
    """Curated plan → network map (issue #33). Blue Value must resolve, and its
    network must exist in the loaded data."""
    r = client.get("/plans")
    assert r.status_code == 200
    body = r.json()
    assert body, "expected at least the curated Blue Value entry"
    for row in body:
        assert {"plan", "network_name", "available"} <= row.keys()
    bv = next((p for p in body if "blue value" in p["plan"].lower()), None)
    assert bv is not None
    assert bv["available"] is True
    assert bv["network_name"] == "GA Blue Value HIX Individual Network"
    # alias search works
    r2 = client.get("/plans", params={"q": "bluevalue"})
    assert any("blue value" in p["plan"].lower() for p in r2.json())


def test_billing_codes_search(client):
    r = client.get("/billing_codes", params={"q": "99213"})
    assert r.status_code == 200
    assert any(row["billing_code"] == "99213" for row in r.json())


@pytest.mark.parametrize("code", CORE_CODES)
def test_core_code_has_rates(client, code):
    r = client.get("/rates/distribution", params={"billing_code": code, "billing_code_type": "CPT"})
    assert r.status_code == 200, f"{code}: {r.text}"
    assert r.json()["summary"]["total_entries"] > 0


@pytest.mark.parametrize("code", KNOWN_GAP_CODES)
@pytest.mark.xfail(reason="known coverage gap — should close as GA files are added", strict=False)
def test_gap_code_now_covered(client, code):
    r = client.get("/rates/distribution", params={"billing_code": code, "billing_code_type": "CPT"})
    assert r.status_code == 200 and r.json()["summary"]["total_entries"] > 0
