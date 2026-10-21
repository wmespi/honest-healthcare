"""Golden-answer tests — pin the product's real answers against the LIVE API.

Unlike test_api_contract.py (hermetic, a synthetic fixture via TestClient),
this hits an actually-running server (`API_URL`, default
http://localhost:8000) and checks its answers against dollar figures and
counts captured from the real corpus (see #96). The store prices the imaging menu only
(serving/service_lines.py PRICED_SERVICE_LINES). The
point is regression, not correctness: if a later step in #95's epic changes
one of these on purpose, recapture it and say why in that PR; if it moves by
accident, this is what's supposed to catch it before a user does.

Skips cleanly (not failing) when the target network isn't loaded, the wider
reference-data build it depends on isn't (NPPES/NUCC, CMS utilization, MPFS,
rate_hist — all "optional" per README.md steps 6-8/#10, the app runs without
them, just differently — GET /'s `reference_loaded` says which are on disk),
or the API isn't reachable at all. Only `make test-live` (run
via docker compose exec, so it shares the container's live localhost:8000)
exercises the real assertions below; a bare `pytest serving/tests/test_golden.py`
with nothing listening skips all of them. `make test`
explicitly exclude this file (Makefile) — it is not hermetic and must never
gate on which build steps happen to have been run.
"""
import os

import httpx
import pytest

API_URL = os.environ.get("API_URL", "http://localhost:8000")
NET = "GA Blue Value HIX Individual Network"


@pytest.fixture(scope="module")
def client():
    # same 120s budget as the journeys harness.
    with httpx.Client(base_url=API_URL, timeout=120) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def _require_live_target_corpus(client):
    try:
        r = client.get("/")
        root = r.json()
    except (httpx.HTTPError, ValueError) as e:
        pytest.skip(f"no API reachable at {API_URL}: {e}")
        return
    if r.status_code != 200 or NET not in root.get("networks", []):
        pytest.skip(f"{NET!r} not loaded at {API_URL} — golden values need the real corpus")

    # The target network alone isn't enough — the pins below also depend on
    # NPPES/NUCC (names, specialties), CMS utilization + MPFS (the "billed"
    # tier and its Medicare benchmark), and rate_hist (the sentinel-price
    # ceiling behind /rates/providers' summary). GET /'s reference_loaded is
    # authoritative (file-exists flags, main.py) — read that directly rather
    # than inferring from an endpoint's shape, which would make a genuine
    # regression in evidence.py/benchmark.py skip instead of fail (the same
    # "billed"/medicare_allowed signals the pins themselves assert on).
    missing = [k for k, loaded in root.get("reference_loaded", {}).items() if not loaded]
    if missing:
        pytest.skip(f"reference data not loaded at {API_URL}: {', '.join(missing)}")


def test_priced_corpus_size(client):
    root = client.get("/").json()
    assert sorted(root["networks"]) == [NET, "Pathway PCP Copay Choice"]
    assert root["total_prices"] == 2819


def test_imaging_70551_emory_quotes_one_flat_price(client):
    # Emory Medical Care Foundation sits in a single fee-schedule bucket for a
    # brain MRI: one office line, so min = typical = max. The price lines that
    # differ by place of service never join an office quote (#129).
    body = client.get("/imaging/providers", params={
        "billing_code": "70551", "network_name": NET, "zip": "30322", "limit": 200,
    }).json()
    emory = next(r for r in body["results"] if r["practice_id"] == "1306002829")
    assert emory["global"] == {"min": 180.49, "typical": 180.49, "max": 180.49}
    assert emory["technical"]["typical"] == 114.01
    assert emory["professional"]["typical"] == 66.48


def test_imaging_70551_carries_a_medicare_facility_estimate(client):
    body = client.get("/imaging/providers", params={"billing_code": "70551", "network_name": NET}).json()
    assert body["facility_estimate"] == {"amount": 243.77, "source": "medicare_opps", "kind": "estimate"}


def test_imaging_70551_practice_count(client):
    body = client.get("/imaging/providers", params={
        "billing_code": "70551", "network_name": NET, "limit": 200,
    }).json()
    assert body["n_practices"] == 89


def test_every_imaging_menu_code_is_priced(client):
    codes = client.get("/service_lines").json()["imaging"]["billing_codes"]
    for code in codes:
        body = client.get("/imaging/providers", params={
            "billing_code": code, "network_name": NET, "limit": 5}).json()
        assert body["n_practices"] > 0, code


def test_imaging_needs_a_plan(client):
    r = client.get("/imaging/providers", params={"billing_code": "70551"})
    assert r.status_code == 400
