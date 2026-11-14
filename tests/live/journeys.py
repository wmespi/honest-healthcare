#!/usr/bin/env python3
"""
User-journey assertions — the pointed "does the real flow still produce the right
answer" checks behind docs/journeys.md.

Unlike test_golden.py beside it (pinned answers), this is depth: for a handful
of named personas, does the specific expected outcome still hold — the canary rate,
the hospital's missing fee, whole prices first, a hospital's fee where the plan's file has one,
one service in every family.

Needs the REAL corpus in the running stack (the assertions check real dollar
figures), so this is a local tool — `make test-live` — not a CI gate.

Also reports latency per journey — total wall time, call count, and the slowest
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
CANARY_70551 = 180.49  # brain MRI at The Emory Clinic on Blue Value — must not move without a known reason
EMORY_CLINIC = "1396798229"
GOLD_PATHWAY = "49046GA0700055"  # Anthem Gold Pathway PCP Copay Choice HMO 1200, Sam's plan here
GRADY = "110079"
GRADY_70551 = 1287.28  # brain MRI at Grady on Pathway: the hospital's fee in the plan's file plus the reading
PEACHTREE_IMMEDIATE_CARE = "1235573841"
SLOW_CALL_MS = 1500   # a single API call slower than this gets flagged (not a failure)

_calls = []  # (path, ms) for the journey currently running — reset per journey


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

def search(api, service_id, **extra):
    return get(api, "/find-care", service_id=service_id, plan_id=SILVER, zip="30322", **extra)


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


def one_bill(service_id, part, place_id, rate, shares, radius_mi=None, kind=None):
    """A journey for a service with one bill: the place shows that one part at the
    rate, no hospital is listed, and the plan's share is under `shares`."""
    def journey(api):
        body, status = search(api, service_id, radius_mi=radius_mi)
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


JOURNEYS = [
    ("J1", "Rosa — brain MRI price near Emory", j1),
    ("J2", "Rosa — knee MRI", j2),
    ("J3", "Sam - brain MRI at a hospital", j3),
    ("J4", "Dana — a search with no plan", j4),
    ("J5", "Rosa - an office visit", one_bill("visit-return-20", "visit", EMORY_CLINIC, 90.5, ["primary_care", "specialist"])),
    ("J6", "Rosa - a yearly checkup", one_bill("checkup-return-40-64", "visit", EMORY_CLINIC, 123.06, ["preventive"])),
    ("J7", "Rosa - urgent care", one_bill("urgent-return-20", "visit", PEACHTREE_IMMEDIATE_CARE, 90.5, ["urgent_care"],
                                         radius_mi=10, kind="urgent_care")),
    ("J8", "Rosa - counseling", one_bill("counseling-45", "session", EMORY_CLINIC, 111.03, ["mental_health"])),
    ("J9", "Rosa - physical therapy", one_bill("therapy-exercise", "therapy", EMORY_CLINIC, 27.64, ["therapy"])),
    ("J10", "Rosa - a lab test", one_bill("lab-cbc-basic", "test", EMORY_CLINIC, 6.47, ["lab"])),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8000/api"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    health, hs = get(args.api, "/health")
    priced = [n["network_id"] for n in (health or {}).get("networks") or [] if n["prices_status"] != "missing"]
    if hs != 200 or BLUE_VALUE not in priced:
        msg = f"{BLUE_VALUE} not loaded at {args.api} — journeys need the real corpus"
        if args.json:
            print(json.dumps({"skipped": True, "reason": msg}))
        else:
            print(f"\n  skipped — {msg}\n")
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
                              if r["slowest_call"]["ms"] == slowest), "—")
        print(f"\n  {n_pass}/{len(results)} journeys pass  ·  "
              f"slowest call {slowest}ms ({slowest_where})")
        print("  (docs/journeys.md has the clickpaths + expected outcomes)\n")

    sys.exit(0 if all(r["pass"] for r in results) else 1)


if __name__ == "__main__":
    main()
