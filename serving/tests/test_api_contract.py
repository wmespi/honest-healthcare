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
    # NPIs 1-5 plus Ng (1000000011, added for the #87 cost-sort test)
    assert body["priceable_npis"] == 6
    assert set(body["networks"]) == {BLUE_VALUE, "GA Blue Open Access POS Network"}
    # the original 5 plus 99204 + 12002 + 99202 (added for the #87 cost-sort
    # / plausible-tier fixture)
    assert body["n_codes"] == 8
    assert body["as_of"] is None or len(body["as_of"]) == 10
    # The fixture builds every optional reference table, including the
    # browse summary (conftest.py runs scripts/build_rate_summary.py, so
    # rate_hist.parquet exists here too).
    assert body["reference_loaded"] == {
        "nppes": True, "nucc": True, "cms_utilization": True,
        "mpfs": True, "rate_hist": True,
    }


def test_distribution_shape(api):
    # code + a network → the live path (per-group counts).
    r = api.get("/rates/distribution",
                params={"billing_code": "99213", "billing_code_type": "CPT",
                        "network_name": BLUE_VALUE})
    assert r.status_code == 200
    body = r.json()
    assert body["billing_code"] == "99213"
    s = body["summary"]
    assert {"min", "max", "avg", "median", "provider_groups", "n_providers", "total_entries"} <= s.keys()
    assert s["min"] <= s["median"] <= s["max"]
    assert s["total_entries"] >= 3
    assert isinstance(body["distribution"], list) and body["distribution"]


def test_distribution_code_without_network_uses_summary(api):
    # code, no network → served off rate_hist (the live expansion spills at
    # GA scale). Per-group counts aren't derivable there.
    body = api.get("/rates/distribution", params={"billing_code": "99213"}).json()
    assert body["billing_code"] == "99213"
    s = body["summary"]
    assert s["provider_groups"] is None and s["n_providers"] is None
    assert s["min"] <= s["median"] <= s["max"]
    assert s["max"] < 900          # the institutional/inpatient noise is out of scope
    assert body["distribution"]


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
    # outpatient-professional scope: the $900 institutional / $500 inpatient
    # 99213 noise rows (conftest) are out, so the pooled max stays sane
    assert s["max"] < 900

    # a network-scoped overview is also served off rate_hist (not a live prices
    # scan) — subset of the all-networks total, per-group counts null, bounded max
    scoped = api.get("/rates/distribution", params={"network_name": BLUE_VALUE}).json()["summary"]
    assert scoped["total_entries"] <= s["total_entries"]
    assert scoped["provider_groups"] is None and scoped["n_providers"] is None
    assert scoped["max"] <= 5000


def test_distribution_rejects_npi_without_code(api):
    r = api.get("/rates/distribution", params={"npi": CARDIOLOGIST})
    assert r.status_code == 400


def test_distribution_specialty_scope_narrows(api):
    # specialty scoping needs the live expansion → a network_name to prune it
    base = api.get("/rates/distribution",
                   params={"billing_code": "99213", "billing_code_type": "CPT",
                           "network_name": BLUE_VALUE}).json()
    scoped = api.get("/rates/distribution",
                     params={"billing_code": "99213", "billing_code_type": "CPT",
                             "network_name": BLUE_VALUE,
                             "specialty": "Cardiovascular Disease"})
    assert scoped.status_code == 200
    assert scoped.json()["summary"]["provider_groups"] <= base["summary"]["provider_groups"]


def test_rates_providers_requires_network(api):
    r = api.get("/rates/providers", params={"billing_code": "99213"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "network_required"
    q = api.get("/rates/quote", params={"billing_code": "99213", "npi": CARDIOLOGIST})
    assert q.status_code == 400
    assert q.json()["detail"]["code"] == "network_required"


def test_rates_providers_shape(api):
    r = api.get("/rates/providers",
                params={"billing_code": "99213", "network_name": BLUE_VALUE, "limit": 10})
    assert r.status_code == 200
    body = r.json()
    results = body["results"]
    assert results
    for row in results:
        assert {"practice_id", "practice_name", "negotiated_rate", "network_name",
                "npi_count", "n_groups"} <= row.keys()
        assert row["min_rate"] >= 1          # sentinel $0.50 row excluded
    assert {"min", "max", "median", "n_practices", "n_groups", "n_providers"} <= body["summary"].keys()
    assert body["summary"]["min"] >= 1


def test_rates_providers_collapses_by_practice(api):
    # groups 10 & 20 both bill under tin_value 1000000010 → a single row for
    # that practice, named from the org NPI, folding both file-local groups.
    r = api.get("/rates/providers",
                params={"billing_code": "99213", "network_name": BLUE_VALUE}).json()
    tens = [row for row in r["results"] if row["practice_id"] == "1000000010"]
    assert len(tens) == 1
    prac = tens[0]
    assert prac["practice_name"] == "Peachtree Internal Medicine LLC"
    assert prac["n_groups"] == 2
    assert prac["npi_count"] == 4          # NPIs 1-4, across the two folded groups
    # one row per practice_id, not per file-local group
    ids = [row["practice_id"] for row in r["results"]]
    assert len(ids) == len(set(ids))


def test_outpatient_scope_excludes_facility_and_percentage(api):
    # conftest adds a $900 institutional, a $500 inpatient, and a 60.0
    # "percentage" (= 60% of charges, not $60) row for 99213 in Blue Value.
    # None may reach a dollar-comparison view — real BV 99213 tops out ~$95.
    prov = api.get("/rates/providers",
                   params={"billing_code": "99213", "network_name": BLUE_VALUE}).json()
    assert prov["summary"]["max"] < 200
    assert all(row["max_rate"] < 200 for row in prov["results"])

    nets = api.get("/rates/by_network", params={"billing_code": "99213"}).json()["networks"]
    bv = next(n for n in nets if n["network_name"] == BLUE_VALUE)
    assert bv["max"] < 200

    dist = api.get("/rates/distribution",
                   params={"billing_code": "99213", "network_name": BLUE_VALUE}).json()
    assert dist["summary"]["max"] < 200

    # inpatient can't narrow an outpatient-scoped view — it's ignored, not empty
    ip = api.get("/rates/providers",
                 params={"billing_code": "99213", "network_name": BLUE_VALUE,
                         "setting": "inpatient"})
    assert ip.status_code == 200 and ip.json()["results"]


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
                                        "npi": 1000000004,  # radiologist in a Blue Value group
                                        "network_name": BLUE_VALUE})
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


def test_quote_carries_medicare_benchmark(api):
    # MPFS benchmark (issue #61): /rates/quote surfaces the GA Medicare allowed
    # amount + a headline/Medicare ratio. 99213 fixture rows: 67.20 (loc 01) +
    # 63.36 (loc 99) nonfacility → median 65.28.
    body = api.get("/rates/quote",
                   params={"billing_code": "99213", "npi": CARDIOLOGIST,
                           "network_name": BLUE_VALUE}).json()
    assert "medicare_allowed" in body and "vs_medicare" in body
    assert body["medicare_allowed"] == 65.28
    assert body["vs_medicare"] == round(body["headline"]["rate"] / 65.28, 2)
    assert body["vs_medicare"] > 0


def test_quote_medicare_billed_flag(api):
    # the cardiologist billed 93000 to Medicare in the fixture
    body = api.get("/rates/quote", params={"billing_code": "93000", "npi": CARDIOLOGIST,
                                           "network_name": BLUE_VALUE}).json()
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
    # CMS Doctors & Clinicians group name is annotated onto each row
    assert hit["group_name"] == "Peachtree Cardiology Associates"


def test_provider_card_carries_dac_identity(api):
    # /providers/{npi}/procedures and /rates/quote both embed provider_card,
    # which gains group_name / years_in_practice / hospital_affiliations when
    # `make doctors-clinicians` has run (the conftest fixture builds it).
    import datetime

    card = api.get(f"/providers/{CARDIOLOGIST}/procedures",
                   params={"network_name": BLUE_VALUE}).json()["provider"]
    assert card["group_name"] == "Peachtree Cardiology Associates"
    assert card["years_in_practice"] == datetime.date.today().year - 2005
    hosp = card["hospital_affiliations"]
    assert isinstance(hosp, list) and len(hosp) == 2
    assert all({"ccn", "facility_name"} <= h.keys() for h in hosp)
    assert {h["ccn"] for h in hosp} == {"110001", "110002"}

    # a solo clinician (no group in the DAC fixture) → group_name null, key
    # still present, affiliations an empty list
    solo = api.get("/providers/1000000006/procedures").json()["provider"]
    assert solo["group_name"] is None
    assert solo["hospital_affiliations"] == []


def test_provider_search_by_specialty(api):
    body = api.get("/providers/search", params={"specialty": "cardio", "limit": 10}).json()
    assert body
    for row in body:
        assert row.get("specialty")
    rated = [x["has_rates"] for x in body]
    assert rated == sorted(rated, reverse=True)  # rated providers rank first


def test_provider_search_specialty_does_not_bleed_across_classification(api):
    # Evans (Psychiatry) and Foster (Neurology) share NUCC classification
    # "Psychiatry & Neurology"; a "Psychiatry" pick must not return the neurologist.
    names = lambda b: {p["name"] for p in b}
    psych = api.get("/providers/search", params={"specialty": "Psychiatry", "limit": 50}).json()
    assert "Evans, Grace" in names(psych)
    assert "Foster, Henry" not in names(psych)
    neuro = api.get("/providers/search", params={"specialty": "Neurology", "limit": 50}).json()
    assert "Foster, Henry" in names(neuro)
    assert "Evans, Grace" not in names(neuro)


def test_provider_search_service_line_pcp(api):
    # #83 — service_line is an exact taxonomy-code allowlist, not a fuzzy label
    # match. Baker (207R00000X, general Internal Medicine) is a PCP code;
    # Adams (207RC0000X, Cardiovascular Disease) is not, even though a naive
    # `classification=Internal Medicine` filter would have caught both — the
    # exact trap `service_line` exists to avoid.
    names = lambda b: {p["name"] for p in b}
    body = api.get("/providers/search", params={"service_line": "pcp", "limit": 50}).json()
    assert "Baker, David" in names(body)
    assert "Adams, Carol" not in names(body)


def test_provider_search_service_line_unknown_is_400(api):
    r = api.get("/providers/search", params={"service_line": "orthopedics"})
    assert r.status_code == 400


def test_provider_search_service_line_cost_sort(api):
    # #87 follow-up — with a plan known, the PCP list ranks cheapest-for-a-
    # new-patient-visit first, not just alphabetically: Ng ($60 via her own
    # group) beats Baker ($150 via his), even though "Baker" < "Ng, Priya"
    # alphabetically. Her group also has a *cheaper* $10 rate on an unrelated
    # code (12002, wound repair) — a decoy: if the sort didn't scope to the
    # PCP billing-code family, it would wrongly pick that $10 row over her
    # real $60 99204 rate.
    body = api.get("/providers/search", params={
        "service_line": "pcp", "network_name": BLUE_VALUE, "limit": 50,
    }).json()
    by_name = {p["name"]: p for p in body}
    assert by_name["Ng, Priya"]["min_rate"] == 60.0
    assert by_name["Baker, David"]["min_rate"] == 150.0
    names_in_order = [p["name"] for p in body if p["name"] in ("Ng, Priya", "Baker, David")]
    assert names_in_order == ["Ng, Priya", "Baker, David"]


def test_provider_search_service_line_cost_sort_prefers_plausible_over_cheaper_group_floor(api):
    # The real bug from live testing (#87 follow-up): a naive MIN() over every
    # in-family price a provider's group can reach picks up Anthem's
    # billing-group fan-out — a network-wide floor rate that has nothing to do
    # with what the provider actually does. Baker has a real $150 rate for
    # 99204 (typical for Internal Medicine, his classification) AND a cheaper
    # $38 rate for 99202 reachable via the same group but not typical for his
    # specialty and never billed by him. min_rate must be the plausible $150,
    # not the cheaper-but-meaningless $38 — and min_rate_is_plausible says so.
    body = api.get("/providers/search", params={
        "service_line": "pcp", "network_name": BLUE_VALUE, "limit": 50,
    }).json()
    baker = next(p for p in body if p["name"] == "Baker, David")
    assert baker["min_rate"] == 150.0
    assert baker["min_rate_is_plausible"] is True


def test_provider_search_service_line_without_network_has_no_min_rate(api):
    # A rate is plan-specific — without `network_name` there's nothing to sort
    # on, so `min_rate` stays null and the existing (alphabetical-ish) order
    # is unchanged, same as before this field existed.
    body = api.get("/providers/search", params={"service_line": "pcp", "limit": 50}).json()
    assert all(p["min_rate"] is None for p in body)
    assert all(p["min_rate_is_plausible"] is None for p in body)


def test_specialties_endpoint(api):
    body = api.get("/specialties", params={"q": "cardio"}).json()
    assert body
    for row in body:
        assert {"specialty", "n_providers", "n_with_rates"} <= row.keys()
        assert row["n_providers"] >= row["n_with_rates"] > 0
    # listed alphabetically — the count is context, not the sort key
    assert [r["specialty"] for r in body] == sorted(r["specialty"] for r in body)


OPEN_ACCESS = "GA Blue Open Access POS Network"


def test_provider_search_has_rates_is_network_scoped(api):
    # Carter (LCSW, NPI 1000000003) is contracted only in Blue Value.
    unscoped = api.get("/providers/search", params={"q": "carter", "limit": 5}).json()
    assert any(p["npi"] == LCSW and p["has_rates"] for p in unscoped)
    bv = api.get("/providers/search",
                 params={"q": "carter", "network_name": BLUE_VALUE, "limit": 5}).json()
    assert any(p["npi"] == LCSW and p["has_rates"] for p in bv)
    oa = api.get("/providers/search",
                 params={"q": "carter", "network_name": OPEN_ACCESS, "limit": 5}).json()
    assert any(p["npi"] == LCSW and not p["has_rates"] for p in oa)


def test_specialties_network_scoped_count(api):
    bv = api.get("/specialties", params={"q": "social", "network_name": BLUE_VALUE}).json()
    assert any("Social Worker" in r["specialty"] and r["n_with_rates"] >= 1 for r in bv)
    oa = api.get("/specialties", params={"q": "social", "network_name": OPEN_ACCESS}).json()
    # the LCSW isn't in Open Access → the specialty has no rated provider there
    assert not any("Social Worker" in r["specialty"] for r in oa)


def test_ga_providers_hospitals_only(api):
    body = api.get("/providers/ga", params={"q": "emory", "hospitals_only": "true", "limit": 5}).json()
    assert body["available"] is True
    assert body["results"]
    for row in body["results"]:
        assert row["is_hospital"] and row["city"]


def test_networks_endpoint(api):
    # served from the browse summary (make build-summary); n_rates is the exact
    # price-row count per network — 17 Blue Value (the original 13, incl. the 3
    # out-of-scope noise rows: rate_summary counts "what exists", not the
    # scoped view; plus 4 for the #87 cost-sort / plausible-tier fixture —
    # Baker's 99204 and 99202 rates, Ng's 99204 rate, and Ng's unrelated $10
    # 12002 row) + 5 Open Access.
    body = api.get("/networks").json()
    counts = {r["network_name"]: r["n_rates"] for r in body}
    assert counts == {BLUE_VALUE: 17, "GA Blue Open Access POS Network": 5}
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
