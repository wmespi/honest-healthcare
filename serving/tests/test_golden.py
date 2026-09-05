"""Golden-answer tests — pin the product's real answers against the LIVE API.

Unlike test_api_contract.py (hermetic, a synthetic fixture via TestClient),
this hits an actually-running server (`API_URL`, default
http://localhost:8000) and checks its answers against dollar figures and
counts captured by hand from the real corpus on 2026-09-05 (see #96). The
point is regression, not correctness: if a later step in #95's epic changes
one of these on purpose, recapture it and say why in that PR; if it moves by
accident, this is what's supposed to catch it before a user does.

Skips cleanly (not failing) when the target network isn't loaded, or the API
isn't reachable at all — CI's compose job runs the stack against an empty
data/ dir and must stay green; a plain `pytest serving/tests/` on a laptop
with no server running must too. Only `make test-live` (or a worktree with
its own stack pointed at the shared data/ via HH_DATA_ROOT) exercises the
real assertions below.
"""
import os

import httpx
import pytest

API_URL = os.environ.get("API_URL", "http://localhost:8000")
NET = "GA Blue Value HIX Individual Network"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=API_URL, timeout=30) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def _require_live_target_network(client):
    try:
        r = client.get("/")
    except httpx.HTTPError as e:
        pytest.skip(f"no API reachable at {API_URL}: {e}")
    if r.status_code != 200 or NET not in r.json().get("networks", []):
        pytest.skip(f"{NET!r} not loaded at {API_URL} — golden values need the real corpus")


def test_root_counts(client):
    # Whole-store totals. Will move once #94's non-target-Parquet parking
    # lands — recapture then, or switch to the target network's
    # /rates/distribution `n` per #96's note.
    body = client.get("/").json()
    assert body["total_prices"] == 645_149_314
    assert body["priceable_npis"] == 75_058


def test_quote_99213_abbasi(client):
    # The standing canary (also asserted by scripts/journeys.py): Suzanne
    # Abbasi personally billed 99213 to Medicare, so this is a "billed"-tier
    # quote, not a group floor.
    body = client.get("/rates/quote", params={
        "billing_code": "99213", "network_name": NET, "npi": 1285125310,
    }).json()
    assert body["tier"] == "billed"
    assert body["headline"]["rate"] == 82.05
    assert body["medicare_allowed"] == 86.75
    assert body["vs_medicare"] == pytest.approx(0.95, abs=0.01)


def test_providers_99213(client):
    # No per-practice identity assertion here on purpose: 200 of 989 practices
    # tie at the $56.84 network floor (all returned at the default `limit`),
    # and which one lands first is not deterministic run-to-run — confirmed by
    # hand, three runs, three different leaders. That's a real, if minor,
    # finding about `/rates/providers`' missing tiebreak, worth its own issue,
    # but Step 0 doesn't touch serving/routers/* to fix it. The aggregate
    # summary is order-independent and exact, so it's what's pinned here.
    body = client.get("/rates/providers", params={
        "billing_code": "99213", "network_name": NET,
    }).json()
    assert body["summary"] == {
        "min": 56.84, "max": 123.08, "avg": 73.78, "median": 90.5,
        "n_rows": 2622, "n_groups": 12, "n_providers": 6566, "n_practices": 989,
    }


def test_providers_search_pcp(client):
    body = client.get("/providers/search", params={
        "service_line": "pcp", "network_name": NET, "limit": 5,
    }).json()
    assert body[0]["npi"] == 1013104652
    assert body[0]["min_rate"] == 37.91


def test_procedures_count(client):
    body = client.get("/providers/1285125310/procedures", params={
        "network_name": NET,
    }).json()
    assert body["count"] == 68


def test_specialties_count(client):
    body = client.get("/specialties", params={"network_name": NET}).json()
    assert len(body) == 60


def test_by_network_45378_row_count(client):
    body = client.get("/rates/by_network", params={"billing_code": "45378"}).json()
    assert len(body["networks"]) == 54


def test_by_network_45378_target_median(client):
    body = client.get("/rates/by_network", params={"billing_code": "45378"}).json()
    target = next(n for n in body["networks"] if n["network_name"] == NET)
    assert target["median"] == 252.81
