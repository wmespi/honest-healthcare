#!/usr/bin/env python3
"""
User-journey assertions — the pointed "does the real flow still produce the right
answer" checks behind docs/journeys.md.

Unlike scripts/frontend_smoke.py (breadth: does every code in a basket have plan
coverage), this is depth: for a handful of named personas, does the specific
expected outcome still hold — the canary rate, the benchmark band, the honest
group-rate tier, the has-rates badge distinction.

Needs the REAL corpus in the running stack (the assertions check real dollar
figures), so this is a local tool — `make journeys` — not a CI gate. CI-safe
browser specs against seeded data are a follow-up (#72).

Also reports latency per journey — total wall time, call count, and the slowest
single call (flagged over SLOW_CALL_MS). Latency is informational: a slow-but-
correct answer doesn't fail the run.

Usage:  python3 scripts/journeys.py [--api http://localhost:8000] [--json]
--api defaults to $API_URL, then http://localhost:8000 -- always pass one
explicitly (or set API_URL) when testing a worktree's own stack; otherwise
this silently tests whatever is on the canonical checkout's port instead.
Exits 0 (skipping every journey, not failing them) if the target network
isn't loaded -- same "no real corpus here" case serving/tests/test_golden.py
skips on (#96). Otherwise exit 0 if every journey's assertions pass, 1
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

NET = "GA Blue Value HIX Individual Network"
CANARY_99213 = 82.05  # 99213 on Blue Value — must not move without a known reason
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


def rows_of(payload):
    """/providers/search returns {data:[...]} or a bare list depending on caller."""
    if isinstance(payload, dict):
        return payload.get("data", [])
    return payload or []


def first_provider_with_quote(api, specialty, code):
    """A provider in the specialty who actually returns a quote for `code` — so
    the assertion doesn't hinge on one hard-coded NPI."""
    search, _ = get(api, "/providers/search", specialty=specialty,
                    network_name=NET, limit=25)
    # a real user picks one provider; cap the "keep trying" fallback so the
    # journey's timing stays representative
    for r in [x for x in rows_of(search) if x.get("has_rates")][:8]:
        q, status = get(api, "/rates/quote", billing_code=code,
                        npi=str(r["npi"]), network_name=NET)
        if status == 200 and q and q.get("headline"):
            return r, q
    return None, None


# ── journeys ────────────────────────────────────────────────────────────────
# each returns (ok: bool, detail: str)

def j1(api):
    prov, q = first_provider_with_quote(api, "Family Medicine", "99213")
    if not q:
        return False, "no Family Medicine provider returned a 99213 quote"
    h = q["headline"]
    checks = [
        (h["rate"] == CANARY_99213, f"rate {h['rate']} (want {CANARY_99213})"),
        (q.get("medicare_allowed", 0) > 0, f"medicare_allowed {q.get('medicare_allowed')}"),
        (0.85 <= (q.get("vs_medicare") or 0) <= 1.05, f"vs_medicare {q.get('vs_medicare')}"),
        (bool((q.get("provider") or {}).get("group_name")), "provider.group_name present"),
    ]
    bad = [msg for ok, msg in checks if not ok]
    who = f"{prov['name']} ({prov['npi']})"
    return (not bad), (f"{who}: " + ("; ".join(bad) if bad else
            f"${h['rate']} · {q['vs_medicare']}× Medicare · {q['provider']['group_name']}"))


def j2(api):
    prov, q = first_provider_with_quote(api, "Family Medicine", "73721")
    if not q:
        return False, "no provider returned a 73721 (knee MRI) quote"
    h = q["headline"]
    ok = bool(h["basis"] == "global" and (q.get("medicare_allowed") or 0) > 0
              and q.get("vs_medicare"))
    return ok, (f"{prov['npi']}: basis {h['basis']} · ${h['rate']} · "
                f"{q.get('vs_medicare')}× Medicare · tier {q.get('tier')}")


def j3(api):
    search, status = get(api, "/providers/search", q="ABBASI", network_name=NET, limit=10)
    rows = rows_of(search)
    if status != 200 or not rows:
        return False, f"search q=ABBASI returned {status} / {len(rows)} rows"
    if not all("has_rates" in r for r in rows):
        return False, "some rows missing the has_rates field (the badge can't render)"
    has = sum(1 for r in rows if r["has_rates"])
    nope = len(rows) - has
    # the point of J3 is the distinction is representable; both sides present is
    # ideal but a name that's all-in or all-out is still a valid answer
    return True, f"{len(rows)} rows · {has} in-plan / {nope} not — badge distinction intact"


def j4(api):
    health, hs = get(api, "/")
    dist, ds = get(api, "/rates/distribution")
    if hs != 200 or ds != 200:
        return False, f"GET / → {hs}, /rates/distribution → {ds}"
    s = dist.get("summary", {})
    checks = [
        ((health.get("priceable_npis") or 0) > 0, "priceable_npis > 0"),
        (len(health.get("networks") or []) > 0, "networks listed"),
        ((s.get("total_entries") or 0) > 100_000_000, f"overview spans {s.get('total_entries'):,} entries (all-network aggregate — documented as misleading)"),
    ]
    bad = [m for ok, m in checks if not ok]
    return (not bad), ("; ".join(bad) if bad else
            f"{health['priceable_npis']:,} NPIs · {len(health['networks'])} networks · "
            f"overview aggregate {s['total_entries']:,} entries")


def j5(api):
    prov, q = first_provider_with_quote(api, "Gastroenterology", "45378")
    if not q:
        return False, "no Gastroenterology provider returned a 45378 (colonoscopy) quote"
    h = q["headline"]
    bn, _ = get(api, "/rates/by_network", billing_code="45378", billing_code_type="CPT")
    n_nets = len(((bn or {}).get("networks")) or [])
    checks = [
        (h["rate"] > 0, f"rate {h['rate']}"),
        (q.get("vs_medicare") and q["vs_medicare"] < 1.0, f"vs_medicare {q.get('vs_medicare')} (< 1.0 expected on Blue Value)"),
        (n_nets >= 2, f"by_network returned {n_nets} networks (cross-plan compare)"),
    ]
    bad = [m for ok, m in checks if not ok]
    return (not bad), (f"{prov['npi']}: " + ("; ".join(bad) if bad else
            f"${h['rate']} · {q['vs_medicare']}× Medicare · tier {q['tier']} · {n_nets} networks compared"))


JOURNEYS = [
    ("J1", "Rosa — routine check-up cost (99213)", j1),
    ("J2", "Rosa — knee MRI (73721)", j2),
    ("J3", "Rosa — is my doctor in this plan?", j3),
    ("J4", "Dana — browsing with no plan", j4),
    ("J5", "Rosa — screening colonoscopy (45378)", j5),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8000"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    health, hs = get(args.api, "/")
    if hs != 200 or NET not in ((health or {}).get("networks") or []):
        msg = f"{NET!r} not loaded at {args.api} — journeys need the real corpus"
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
