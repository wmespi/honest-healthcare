"""Golden-answer tests — pin the product's real answers against the LIVE API.

Unlike test_api_contract.py (hermetic, a synthetic fixture via TestClient),
this hits an actually-running server (`API_URL`, default
http://localhost:8000) and checks its answers against dollar figures and
counts captured by hand from the real corpus on 2026-09-04 (see #96). The
point is regression, not correctness: if a later step in #95's epic changes
one of these on purpose, recapture it and say why in that PR; if it moves by
accident, this is what's supposed to catch it before a user does.

Skips cleanly (not failing) when the target network isn't loaded, the wider
reference-data build it depends on isn't (NPPES/NUCC, CMS utilization, MPFS —
all "optional" per README.md steps 6-8, the app runs without them, just
differently), or the API isn't reachable at all. Only `make test-live` (run
via docker compose exec, so it shares the container's live localhost:8000)
exercises the real assertions below; a bare `pytest serving/tests/test_golden.py`
with nothing listening skips all of them. `make test-api` / `make test-all`
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
    # /rates/by_network scans every GA network with no cache — 10-20s+ warm,
    # worse cold or under DUCKDB_MEMORY_LIMIT=2GB (a worktree's default).
    # test_coverage.py uses the same 120s for the same reason.
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
    # NPPES/NUCC (names, specialties) and CMS utilization + MPFS (the
    # "billed" tier and its Medicare benchmark). Probe the cheapest live
    # endpoint that needs each, rather than reaching past the API into
    # data_sources.py's paths.
    try:
        specialties = client.get("/specialties", params={"network_name": NET, "limit": 1}).json()
    except (httpx.HTTPError, ValueError) as e:
        pytest.skip(f"/specialties unreachable/non-JSON at {API_URL}: {e}")
        return
    if not specialties:
        pytest.skip("NPPES/NUCC reference data not loaded — /specialties returned none")

    try:
        quote = client.get("/rates/quote", params={
            "billing_code": "99213", "network_name": NET, "npi": 1285125310,
        }).json()
    except (httpx.HTTPError, ValueError) as e:
        pytest.skip(f"/rates/quote unreachable/non-JSON at {API_URL}: {e}")
        return
    if quote.get("tier") != "billed" or quote.get("medicare_allowed") is None:
        pytest.skip("CMS utilization / MPFS reference data not loaded — the canary "
                    "quote isn't a 'billed'-tier quote with a Medicare benchmark")


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


def test_distribution_99213_target_network(client):
    # Network-scoped and live off `prices`/`group_sets` regardless of
    # whether the browse summary (rate_summary/code_rollup, #10) has been
    # built. GET /'s total_prices/priceable_npis are NOT used for this kind
    # of pin: main.py silently switches their source on have_summary(), so
    # they can disagree with what /rates/quote and this endpoint themselves
    # read, and would move on a routine `make parse` + `make build-summary`
    # gap, not just once #94 parks non-target Parquet. This is the closest
    # thing to a stable "is the corpus what we think it is" check until a
    # whole-store equivalent lands (#96's own suggested fallback).
    body = client.get("/rates/distribution", params={
        "billing_code": "99213", "network_name": NET,
    }).json()
    s = body["summary"]
    assert s["provider_groups"] == 34
    assert s["n_providers"] == 22710
    assert s["total_entries"] == 62
    assert s["median"] == 82.05


def test_providers_99213(client):
    # No per-practice identity assertion here on purpose: 200 of 989
    # practices tie at the $56.84 network floor (all returned at the default
    # `limit`), and which one lands first is not deterministic run-to-run —
    # confirmed by hand, three runs, three different leaders (no `ORDER BY`
    # tiebreak past (min_rate, max_rate) in rates.py). That's a real, if
    # minor, finding about /rates/providers' missing tiebreak, worth its own
    # issue, but Step 0 doesn't touch serving/routers/* to fix it. The
    # aggregate summary is order-independent and exact, so it's what's
    # pinned here.
    body = client.get("/rates/providers", params={
        "billing_code": "99213", "network_name": NET,
    }).json()
    assert body["summary"] == {
        "min": 56.84, "max": 123.08, "avg": 73.78, "median": 90.5,
        "n_rows": 2622, "n_groups": 12, "n_providers": 6566, "n_practices": 989,
    }


def test_providers_search_pcp(client):
    # Same shape of risk as above, one level less severe: 16 PCPs tie at the
    # $37.91 network floor, and providers.py's ORDER BY breaks ties on
    # `name` (alphabetical) with no `npi` tiebreak after it — a routine
    # NPPES refresh adding an earlier-alphabetical name at the same floor
    # would flip who lands in slot 0 with zero rate change. min_rate is
    # stable regardless of tie order; membership within the full tied group
    # (limit=25 comfortably covers all 16) is the most a name-sorted list
    # can promise for one specific identity.
    body = client.get("/providers/search", params={
        "service_line": "pcp", "network_name": NET, "limit": 25,
    }).json()
    assert body[0]["min_rate"] == 37.91
    assert 1013104652 in {r["npi"] for r in body}


def test_procedures_count(client):
    body = client.get("/providers/1285125310/procedures", params={
        "network_name": NET,
    }).json()
    assert body["count"] == 68


def test_specialties_count(client):
    # limit defaults to 60 (providers.py) -- without an explicit limit this
    # pins the route's default page size, not a corpus fact.
    body = client.get("/specialties", params={"network_name": NET, "limit": 500}).json()
    assert len(body) == 297


def test_by_network_45378(client):
    # One call backing two assertions, not two calls — this is the slowest
    # query in the file; issuing it twice roughly doubles this file's wall
    # time and its exposure to a client-side timeout for no benefit.
    body = client.get("/rates/by_network", params={"billing_code": "45378"}).json()
    assert len(body["networks"]) == 54
    target = next(n for n in body["networks"] if n["network_name"] == NET)
    assert target["median"] == 252.81
