#!/usr/bin/env python3
"""
User-journey assertions - the pointed "does the real flow still produce the right
answer" checks behind docs/journeys.md.

Unlike test_golden.py beside it (pinned answers), this is depth: for a handful
of named personas, does the specific expected outcome still hold - the canary rate,
the hospital's missing fee, whole prices first, a hospital's fee where the plan's file has one,
one service in every family.

Needs the REAL corpus in the running stack (the assertions check real dollar
figures), so this is a local tool - `make test-live` - not a CI gate.

Also reports latency per journey - total wall time, call count, and the slowest
single call (flagged over SLOW_CALL_MS). Latency is informational: a slow-but-
correct answer doesn't fail the run.

Usage:  python3 tests/live/journeys.py [--api http://localhost:8000/api] [--json]
--api defaults to $API_URL, then http://localhost:8000/api -- always pass one
explicitly (or set API_URL) when testing a worktree's own stack; otherwise
this silently tests whatever is on the canonical checkout's port instead.
Exits 0 (skipping every journey, not failing them) if the target network
isn't loaded -- same "no real corpus here" case test_golden.py
skips on. Otherwise exit 0 if every journey's assertions pass, 1
otherwise (latency never fails it).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BLUE_VALUE = "45334-GAN001"
SILVER = "45334GA0020002"  # Anthem Silver Blue Value HMO 4000, Rosa's plan here
CANARY_70551 = 180.49  # brain MRI at The Emory Clinic on Blue Value - must not move without a known reason
EMORY_CLINIC = "1396798229"
GOLD_PATHWAY = "49046GA0700055"  # Anthem Gold Pathway PCP Copay Choice HMO 1200, Sam's plan here
GRADY = "110079"
EMORY_MIDTOWN = "110078"
EMORY_HOSPITAL = "110010"
EYE_PHYSICIANS = "1720011422"  # Eye Physicians and Surgeons, a surgery center
DERMATOLOGY_ASSOCIATES = "1063560803"  # Dermatology Associates of Georgia
GRADY_70551 = 1287.28  # brain MRI at Grady on Pathway: the hospital's fee in the plan's file plus the reading
PEACHTREE_IMMEDIATE_CARE = "1235573841"
# Anthem Catastrophic Pathway Guided Access HMO 10600 First 3 PCP Visits $40, Jordan's plan here
GUIDED_CATASTROPHIC = "49046GA0700010"
AMBETTER_SILVER = "70893GA0010077"  # Ambetter Standard Silver HMO $6000 $40, on Premier, Lee's plan here
INDEPENDENT_PT = "1356494686"  # Independent Physical Therapy of Georgia
SLOW_CALL_MS = 1500   # a single API call slower than this gets flagged (not a failure)

_calls = []  # (path, ms) for the journey currently running - reset per journey


def get(api, path, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{api}{path}?{q}" if q else f"{api}{path}"
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            payload, status = json.load(r), r.status
    except urllib.error.HTTPError as e:
        payload, status = None, e.code
    except Exception as e:  # noqa: BLE001
        payload, status = None, f"error: {e}"
    _calls.append((path, (time.perf_counter() - t0) * 1000))
    return payload, status


# ── journeys ────────────────────────────────────────────────────────────────
# each returns (ok: bool, detail: str)

def search(api, service_id, plan_id=SILVER, **extra):
    return get(api, "/find-care", service_id=service_id, plan_id=plan_id, zip="30322", **extra)


def j1(api):
    body, status = search(api, "mri-brain")
    rows = (body or {}).get("results") or []
    if status != 200 or not rows:
        return False, f"/find-care mri-brain returned {status} / {len(rows)} places"
    clinic = next((r for r in rows if r["place_id"] == EMORY_CLINIC), None)
    hospitals = [r for r in rows if r["kind"] == "hospital"]
    checks = [
        (clinic is not None, "The Emory Clinic listed"),
        (clinic and clinic["max_rate"] == CANARY_70551, f"Emory Clinic {clinic and clinic['max_rate']} (want {CANARY_70551})"),
        ([s["benefit"] for s in body.get("cost_sharing") or []] == ["advanced_imaging"], "the plan's share of the rate"),
        (all(h["max_rate"] is None and h["parts"][0]["billing_class"] == "institutional"
             and h["parts"][0]["max"] is None and h["parts"][0]["missing_explain"] for h in hospitals),
         "a hospital's own fee shows as missing, so it has no most you'd pay"),
        (all(r["name"] and r["city"] and r["distance_mi"] is not None for r in rows),
         "every place has its own name, a city and a distance from the ZIP"),
    ]
    bad = [msg for ok, msg in checks if not ok]
    return (not bad), ("; ".join(bad) if bad else
            f"{len(rows)} places · Emory Clinic ${CANARY_70551} · nearest {min(r['distance_mi'] for r in rows)} mi")


def j2(api):
    body, status = search(api, "mri-knee", radius_mi=10)
    rows = (body or {}).get("results") or []
    if status != 200 or not rows:
        return False, f"/find-care mri-knee returned {status} / {len(rows)} places"
    whole = [r["max_rate"] is not None for r in rows]
    ok = any(whole) and whole == sorted(whole, reverse=True)
    return ok, f"{len(rows)} places · {sum(whole)} with a whole price, listed first"


def j3(api):
    body, status = get(api, "/find-care", service_id="mri-brain", plan_id=GOLD_PATHWAY, zip="30303")
    rows = (body or {}).get("results") or []
    grady = next((r for r in rows if r["place_id"] == GRADY), None)
    if status != 200 or grady is None:
        return False, f"/find-care mri-brain returned {status} / {len(rows)} places, Grady {'listed' if grady else 'missing'}"
    fee = grady["parts"][0]
    checks = [
        (fee["part"] == "facility" and fee["source"] == "plan", f"Grady's fee is {fee} (want the plan's file)"),
        (grady["max_rate"] == GRADY_70551, f"Grady {grady['max_rate']} (want {GRADY_70551})"),
        (grady["max_rate"] == round(sum(p["max"] or 0 for p in grady["parts"]), 2), "the most you'd pay is the fee plus the reading"),
        (grady["cash_price"] is not None, "the hospital's price without insurance"),
    ]
    bad = [msg for ok, msg in checks if not ok]
    return (not bad), ("; ".join(bad) if bad else
            f"Grady ${GRADY_70551} = fee ${fee['max']} + reading · ${grady['cash_price']:,.0f} without insurance")


def j4(api):
    body, status = get(api, "/find-care", service_id="mri-brain", zip="30322")
    return status == 422, f"no plan -> {status} (want 422: prices depend on the plan)"


def one_bill(service_id, part, place_id, rate, shares, radius_mi=None, kind=None, specialty=None, plan_id=SILVER):
    """A journey for a service with one bill: the place shows that one part at the
    rate, no hospital is listed, and the plan's share is under `shares`."""
    def journey(api):
        body, status = search(api, service_id, plan_id=plan_id, radius_mi=radius_mi, specialty=specialty)
        rows = (body or {}).get("results") or []
        if status != 200 or not rows:
            return False, f"/find-care {service_id} returned {status} / {len(rows)} places"
        place = next((r for r in rows if r["place_id"] == place_id), None)
        got = place and [(p["part"], p["max"]) for p in place["parts"]]
        benefits = [s["benefit"] for s in body.get("cost_sharing") or []]
        checks = [
            (place is not None, f"place {place_id} listed"),
            (got == [(part, rate)] and place["max_rate"] == rate, f"{place_id} shows {got} (want {part} at {rate})"),
            (all(r["kind"] != "hospital" for r in rows), "no hospital is listed"),
            (kind is None or all(r["kind"] == kind for r in rows), f"every place is a {kind}"),
            (benefits == shares, f"the plan's share is under {benefits} (want {shares})"),
        ]
        bad = [msg for ok, msg in checks if not ok]
        return (not bad), ("; ".join(bad) if bad else f"{len(rows)} places · {place['name']} ${rate}")
    return journey


def procedure(service_id, plan_id, place_id, parts, radius_mi=None):
    """A journey for a procedure: the place shows its bills in order as (part,
    rate, source), the most you'd pay is their sum only when none is missing, and
    the plan's share is under its outpatient surgery and facility benefits."""
    def journey(api):
        body, status = get(api, "/find-care", service_id=service_id, plan_id=plan_id, zip="30322",
                            radius_mi=radius_mi)
        rows = (body or {}).get("results") or []
        if status != 200 or not rows:
            return False, f"/find-care {service_id} returned {status} / {len(rows)} places"
        place = next((r for r in rows if r["place_id"] == place_id), None)
        got = place and [(p["part"], p["max"], p["source"]) for p in place["parts"]]
        rates = [rate for _, rate, _ in parts]
        whole = None if None in rates else round(sum(rates), 2)
        benefits = [s["benefit"] for s in body.get("cost_sharing") or []]
        checks = [
            (place is not None, f"place {place_id} listed"),
            (got == parts, f"{place_id} shows {got} (want {parts})"),
            (place and place["max_rate"] == whole, f"the most you'd pay is {place and place['max_rate']} (want {whole})"),
            (benefits == ["outpatient_surgery", "outpatient_facility"], f"the plan's share is under {benefits}"),
        ]
        bad = [msg for ok, msg in checks if not ok]
        return (not bad), ("; ".join(bad) if bad else f"{len(rows)} places · {place['name']} {got}")
    return journey


def j18(api):
    """Sam: quality next to a colonoscopy's price, at a hospital and for its doctors."""
    body, status = get(api, "/find-care", service_id="colonoscopy", plan_id=GOLD_PATHWAY, zip="30322")
    rows = (body or {}).get("results") or []
    midtown = next((r for r in rows if r["place_id"] == EMORY_MIDTOWN), None)
    if status != 200 or midtown is None:
        return False, f"/find-care colonoscopy returned {status}, Emory Midtown {'listed' if midtown else 'missing'}"
    first = midtown["quality"][0] if midtown["quality"] else {}
    counts = [q["shown"] for c in midtown["providers"] for q in c["quality"]]
    checks = [
        ((first.get("measure"), first.get("shown"), first.get("compared")) ==
         ("OP-32", "12.9 per 1,000", "No different than the national rate"),
         f"Emory Midtown's first measure is {first} (want OP-32, 12.9 per 1,000, no different)"),
        (bool(first.get("period")) and bool(first.get("source")), "the measure says its period and source"),
        (bool(counts), "a doctor there shows how many colonoscopies they've done"),
    ]
    bad = [msg for ok, msg in checks if not ok]
    return (not bad), ("; ".join(bad) if bad else f"OP-32 {first['shown']} · doctors' counts {counts[:3]}")


def j19(api):
    """Both plans: the bills a colonoscopy with biopsy also brings, outside the places' prices."""
    got = {}
    for who, plan in (("Sam", GOLD_PATHWAY), ("Rosa", SILVER)):
        body, status = get(api, "/find-care", service_id="colonoscopy-biopsy", plan_id=plan, zip="30322")
        if status != 200:
            return False, f"/find-care colonoscopy-biopsy for {who} returned {status}"
        got[who] = {e["part"]: e for e in body.get("extra_bills") or []}
    sam, rosa = got["Sam"], got["Rosa"]
    checks = [
        (sam.get("anesthesia", {}).get("typical") == 232.5, f"Sam's anesthesia is {sam.get('anesthesia')} (want usually 232.5)"),
        (sam.get("pathology", {}).get("typical") == 98.95, f"Sam's lab exam is {sam.get('pathology')} (want usually 98.95)"),
        (rosa.get("pathology", {}).get("typical") == 48.78, f"Rosa's lab exam is {rosa.get('pathology')} (want usually 48.78)"),
        (rosa.get("anesthesia", {}).get("typical", 0) is None and "no rate" in rosa.get("anesthesia", {}).get("note", ""),
         "Rosa's plan lists no anesthesia rate, and says so"),
    ]
    bad = [msg for ok, msg in checks if not ok]
    return (not bad), ("; ".join(bad) if bad else "Sam: anesthesia ~$232, lab ~$99 · Rosa: lab ~$49, no anesthesia rate")


def j20(api):
    """Sam: having a baby, at hospitals priced per stay or per day."""
    body, status = get(api, "/find-care", service_id="stay-vaginal-delivery", plan_id=GOLD_PATHWAY, zip="30303",
                       radius_mi=10)
    rows = {r["place_id"]: r for r in (body or {}).get("results") or []}
    midtown, grady = rows.get(EMORY_MIDTOWN), rows.get(GRADY)
    if status != 200 or not midtown or not grady:
        return False, f"/find-care stay-vaginal-delivery returned {status}, Midtown {bool(midtown)}, Grady {bool(grady)}"
    stay = lambda r: (r["parts"][0]["part"], r["parts"][0]["max"], r["parts"][0]["source"], r["parts"][0]["terms"])
    parts = {e["part"] for e in body.get("extra_bills") or []}
    checks = [
        (stay(midtown) == ("stay", 9268.87, "plan", None), f"Emory Midtown's stay is {stay(midtown)}"),
        (stay(grady) == ("stay", None, "hospital", "$8,000 a day") and grady["max_rate"] is None,
         f"Grady's stay is {stay(grady)} (want $8,000 a day, from its own price list, no total)"),
        (parts == {"doctor", "anesthesia"}, f"also billed: {sorted(parts)} (want the doctor and anesthesia)"),
    ]
    bad = [msg for ok, msg in checks if not ok]
    return (not bad), ("; ".join(bad) if bad else "Midtown $9,268.87 · Grady $8,000 a day · doctor and anesthesia apart")


def j21(api):
    """Rosa: having a baby on a plan whose file lists no hospital's stay price."""
    body, status = get(api, "/find-care", service_id="stay-vaginal-delivery", plan_id=SILVER, zip="30322")
    extra = {e["part"]: e["typical"] for e in (body or {}).get("extra_bills") or []}
    checks = [
        (status == 200 and not body["results"] and body.get("closest_outside") is None, "no hospital shows a price"),
        (bool(body.get("empty_note")), "the screen says why"),
        (extra == {"doctor": 2217.89, "anesthesia": 253.41}, f"also billed: {extra} (want the doctor ~$2,218, epidural ~$253)"),
    ]
    bad = [msg for ok, msg in checks if not ok]
    return (not bad), ("; ".join(bad) if bad else "no stay prices, says why · doctor ~$2,218 · epidural ~$253")


def j22(api):
    """Jordan: a primary care visit on a catastrophic plan, whose copay covers only its first visits."""
    body, status = get(api, "/find-care", service_id="visit-return-20", plan_id=GUIDED_CATASTROPHIC, zip="30322",
                       specialty="primary_care")
    rows = (body or {}).get("results") or []
    clinic = next((r for r in rows if r["place_id"] == EMORY_CLINIC), None)
    got = clinic and [(p["part"], p["max"]) for p in clinic["parts"]]
    share = ((body or {}).get("cost_sharing") or [{}])[0]
    checks = [
        (status == 200 and clinic is not None, f"/find-care returned {status}; The Emory Clinic listed"),
        (got == [("visit", 126.71)], f"its visit is {got} (want $126.71)"),
        ((share.get("copay"), share.get("copay_limited"), share.get("after_deductible")) == (40.0, True, True),
         f"the share is {share} (want a $40 copay for the first visits, then the deductible)"),
    ]
    bad = [msg for ok, msg in checks if not ok]
    return (not bad), ("; ".join(bad) if bad else "Emory Clinic $126.71 · $40 copay for the first visits")


JOURNEYS = [
    ("J1", "Rosa - brain MRI price near Emory", j1),
    ("J2", "Rosa - knee MRI", j2),
    ("J3", "Sam - brain MRI at a hospital", j3),
    ("J4", "Dana - a search with no plan", j4),
    ("J5", "Rosa - a primary care visit", one_bill("visit-return-20", "visit", EMORY_CLINIC, 90.5, ["primary_care"],
                                                 specialty="primary_care")),
    ("J6", "Rosa - a yearly checkup", one_bill("checkup-return-40-64", "visit", EMORY_CLINIC, 123.06, ["preventive"])),
    ("J7", "Rosa - urgent care", one_bill("urgent-return-20", "visit", PEACHTREE_IMMEDIATE_CARE, 90.5, ["urgent_care"],
                                         radius_mi=10, kind="urgent_care")),
    ("J8", "Rosa - counseling", one_bill("counseling-45", "session", EMORY_CLINIC, 111.03, ["mental_health"])),
    ("J9", "Rosa - physical therapy", one_bill("therapy-exercise", "therapy", EMORY_CLINIC, 27.64, ["therapy"])),
    ("J10", "Rosa - a lab test", one_bill("lab-cbc-basic", "test", EMORY_CLINIC, 6.47, ["lab"])),
    ("J11", "Sam - a colonoscopy at a hospital", procedure(
        "colonoscopy", GOLD_PATHWAY, EMORY_MIDTOWN, [("facility", 2678.54, "hospital"), ("surgeon", 594.1, "plan")])),
    ("J12", "Rosa - a colonoscopy", procedure(
        "colonoscopy", SILVER, EMORY_CLINIC, [("facility", None, None), ("surgeon", 162.46, "plan")])),
    ("J13", "Sam - cataract surgery at a surgery center", procedure(
        "eye-cataract", GOLD_PATHWAY, EYE_PHYSICIANS, [("facility", 830.0, "plan"), ("surgeon", 643.57, "plan")])),
    ("J14", "Sam - a knee scope at a hospital", procedure(
        "joint-knee-scope-meniscus", GOLD_PATHWAY, EMORY_HOSPITAL,
        [("facility", 3514.53, "hospital"), ("surgeon", None, None)])),
    ("J15", "Rosa - a skin biopsy in the office", procedure(
        "skin-biopsy", SILVER, DERMATOLOGY_ASSOCIATES, [("in_office", 85.47, "plan")])),
    ("J16", "Sam - gallbladder removal at a hospital", procedure(
        "surgery-gallbladder-scope", GOLD_PATHWAY, EMORY_HOSPITAL,
        [("facility", 4962.02, "hospital"), ("surgeon", None, None)])),
    ("J17", "Rosa - a visit with a skin doctor", one_bill("visit-return-20", "visit", DERMATOLOGY_ASSOCIATES, 82.05,
                                                     ["specialist"], specialty="skin")),
    ("J18", "Sam - quality next to a colonoscopy", j18),
    ("J19", "Both - what else a colonoscopy with biopsy brings", j19),
    ("J20", "Sam - having a baby", j20),
    ("J21", "Rosa - having a baby", j21),
    ("J22", "Jordan - a primary care visit", j22),
    ("J23", "Lee - cataract surgery on Ambetter", procedure(
        "eye-cataract", AMBETTER_SILVER, EMORY_HOSPITAL, [("facility", 4907.38, "hospital"), ("surgeon", 728.86, "plan")])),
    ("J24", "Lee - physical therapy on Ambetter", one_bill("therapy-exercise", "therapy", INDEPENDENT_PT, 24.69,
                                                          ["therapy"], plan_id=AMBETTER_SILVER)),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8000/api"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    health, hs = get(args.api, "/health")
    priced = [n["network_id"] for n in (health or {}).get("networks") or [] if n["prices_status"] != "missing"]
    if hs != 200 or BLUE_VALUE not in priced:
        msg = f"{BLUE_VALUE} not loaded at {args.api} - journeys need the real corpus"
        if args.json:
            print(json.dumps({"skipped": True, "reason": msg}))
        else:
            print(f"\n  skipped - {msg}\n")
        sys.exit(0)

    results = []
    for jid, name, fn in JOURNEYS:
        _calls.clear()
        t0 = time.perf_counter()
        try:
            ok, detail = fn(args.api)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"raised {type(e).__name__}: {e}"
        total_ms = (time.perf_counter() - t0) * 1000
        slow_path, slow_ms = max(_calls, key=lambda c: c[1], default=("", 0.0))
        results.append({
            "id": jid, "name": name, "pass": ok, "detail": detail,
            "ms_total": round(total_ms), "n_calls": len(_calls),
            "slowest_call": {"path": slow_path, "ms": round(slow_ms)},
        })

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\n  {'':4} {'journey':38} {'':4} {'total':>7} {'calls':>6}  detail")
        print("  " + "-" * 104)
        for r in results:
            mark = "\033[32mPASS\033[0m" if r["pass"] else "\033[31mFAIL\033[0m"
            slow = r["slowest_call"]
            flag = (f"  \033[33m⚠ {slow['path']} {slow['ms']}ms\033[0m"
                    if slow["ms"] > SLOW_CALL_MS else "")
            print(f"  {r['id']:4} {r['name']:38} {mark} {r['ms_total']:>5}ms "
                  f"{r['n_calls']:>6}  {r['detail']}{flag}")
        n_pass = sum(1 for r in results if r["pass"])
        slowest = max((r["slowest_call"]["ms"] for r in results), default=0)
        slowest_where = next((f"{r['slowest_call']['path']} in {r['id']}"
                              for r in results
                              if r["slowest_call"]["ms"] == slowest), "-")
        print(f"\n  {n_pass}/{len(results)} journeys pass  ·  "
              f"slowest call {slowest}ms ({slowest_where})")
        print("  (docs/journeys.md has the clickpaths + expected outcomes)\n")

    sys.exit(0 if all(r["pass"] for r in results) else 1)


if __name__ == "__main__":
    main()
