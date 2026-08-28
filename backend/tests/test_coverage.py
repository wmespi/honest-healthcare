"""
Backend contract + coverage tests.

Contract tests (must pass): endpoints return 200 with the expected shape.
Coverage tests (xfail — monitoring signal, not a red build): the primary-use-case
basket of procedure codes resolves to rates.

Runs against a live backend. Point at it with API_URL (default http://localhost:8000).
Inside the backend container use http://localhost:8000.
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
    assert body.get("total_rates", 0) > 0


def test_distribution_shape(client):
    r = client.get("/rates/distribution", params={"billing_code": "99213", "billing_code_type": "CPT"})
    assert r.status_code == 200
    body = r.json()
    assert body["billing_code"] == "99213"
    for k in ("min", "max", "avg", "median", "provider_groups", "total_entries"):
        assert k in body["summary"]
    assert isinstance(body["distribution"], list) and body["distribution"]


def test_providers_shape(client):
    r = client.get("/rates/providers", params={"billing_code": "99213", "limit": 5})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results
    for row in results:
        assert {"provider_group_id", "negotiated_rate", "network_name", "npi_count"} <= row.keys()


def test_networks_endpoint(client):
    r = client.get("/networks")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)  # [] until a post-attribution parse lands


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
