#!/usr/bin/env python3
"""
Frontend route smoke test — exercises exactly the API calls the rate explorer
makes (api.js), for the mother's plan network, across a wide procedure basket.

For each procedure it checks:
  - /billing_codes?q=<code>            (the search box)
  - /rates/distribution (network-wide) (the default histogram)
  - /rates/distribution?network_name=… (the network filter — the mother's plan)
  - /rates/providers?network_name=…    (provider drill-down, incl. GA hospitals)

Prints a table; exits non-zero if the network filter finds nothing for a code
that exists network-wide (a real UI gap for that plan).

Usage: python3 scripts/frontend_smoke.py [--api http://localhost:8000]
"""
import argparse
import sys
import urllib.parse
import urllib.request
import json

NETWORK = "GA Blue Value HIX Individual Network"

BASKET = {
    "99213": "Office visit, established (L3)",
    "99214": "Office visit, established (L4)",
    "99204": "Office visit, new (L4)",
    "99395": "Preventive visit, established 18-39",
    "99385": "Preventive visit, new 18-39",
    "80053": "Comprehensive metabolic panel",
    "80061": "Lipid panel",
    "85025": "CBC with differential",
    "83036": "Hemoglobin A1c",
    "84443": "TSH",
    "81001": "Urinalysis, automated w/ microscopy",
    "87070": "Bacterial culture",
    "71046": "Chest X-ray, 2 views",
    "70450": "CT head w/o contrast",
    "72148": "MRI lumbar spine w/o contrast",
    "73721": "MRI lower extremity joint w/o contrast",
    "76700": "Abdominal ultrasound, complete",
    "77067": "Screening mammography, bilateral",
    "93000": "ECG, 12-lead w/ interpretation",
    "93306": "Echocardiogram, complete w/ Doppler",
    "45378": "Colonoscopy, diagnostic",
    "45380": "Colonoscopy w/ biopsy",
    "43239": "Upper GI endoscopy w/ biopsy",
    "29881": "Knee arthroscopy w/ meniscectomy",
    "27447": "Total knee replacement",
    "47562": "Laparoscopic cholecystectomy",
    "66984": "Cataract surgery w/ IOL",
    "49505": "Inguinal hernia repair",
    "59400": "Routine obstetric care w/ vaginal delivery",
    "59510": "Routine obstetric care w/ cesarean delivery",
    "63030": "Lumbar laminotomy / discectomy",
    "97110": "Therapeutic exercise, 15 min",
    "97140": "Manual therapy, 15 min",
    "98940": "Chiropractic manipulation, 1-2 regions",
    "90837": "Psychotherapy, 60 min",
    "90834": "Psychotherapy, 45 min",
    "90791": "Psychiatric diagnostic evaluation",
    "96372": "Therapeutic injection, SC/IM",
    "90686": "Flu vaccine, quadrivalent",
    "20610": "Major joint injection/aspiration",
    "11042": "Debridement, subcutaneous tissue",
    "17000": "Destruction, premalignant lesion",
    "99283": "ER visit, moderate severity",
    "99284": "ER visit, high severity",
    "00810": "Anesthesia, lower intestinal endoscopy",
}


def get(api, path, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{api}{path}?{q}" if q else f"{api}{path}"
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.load(r), r.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    args = ap.parse_args()

    health, _ = get(args.api, "/")
    nets, _ = get(args.api, "/networks")
    net_names = [n["network_name"] for n in (nets or [])]
    print(f"backend: {health.get('total_rates'):,} rates")
    print(f"target network present in /networks: {NETWORK in net_names}")
    if NETWORK not in net_names:
        print("  !! the mother's-plan network is not in the data — UI filter will be empty", file=sys.stderr)

    print(f"\n{'code':7} {'procedure':44} {'search':7} {'net-wide':9} {'in-plan':9} {'providers':10} {'GA hosp':8}")
    print("-" * 100)
    gaps, hard_fail = [], []
    for code, name in BASKET.items():
        sr, _ = get(args.api, "/billing_codes", q=code)
        search_ok = any(row["billing_code"] == code for row in (sr or []))

        wide, _ = get(args.api, "/rates/distribution", billing_code=code, billing_code_type="CPT")
        wide_n = (wide or {}).get("summary", {}).get("total_entries", 0)

        plan, _ = get(args.api, "/rates/distribution", billing_code=code,
                      billing_code_type="CPT", network_name=NETWORK)
        plan_s = (plan or {}).get("summary", {})
        plan_n = plan_s.get("total_entries", 0)

        prov, _ = get(args.api, "/rates/providers", billing_code=code,
                      billing_code_type="CPT", network_name=NETWORK, limit=200)
        prov_rows = (prov or {}).get("results", [])
        ga_hosp = sum(1 for r in prov_rows if r.get("ga_hospital_npis", 0))

        plan_str = (f"${plan_s['min']:.0f}-${plan_s['max']:.0f}" if plan_n else "—")
        print(f"{code:7} {name[:44]:44} {'ok' if search_ok else 'MISS':7} "
              f"{wide_n:<9} {plan_n:<9} {len(prov_rows):<10} {ga_hosp:<8} {plan_str}")

        # "not searchable" is only a real failure when the code HAS rates — a
        # code absent everywhere (e.g. 00810, priced in base units) legitimately
        # won't be in the codes parquet.
        if wide_n > 0 and not search_ok:
            hard_fail.append(f"{code}: has rates but not searchable")
        if wide_n > 0 and plan_n == 0:
            gaps.append(f"{code} ({name})")
        if wide_n == 0:
            gaps.append(f"{code} ({name}) — no rates anywhere")

    print()
    if gaps:
        print(f"IN-PLAN GAPS ({len(gaps)}): codes with network-wide rates but none for {NETWORK}:")
        for g in gaps:
            print(f"  - {g}")
    else:
        print(f"No in-plan gaps — every code with rates has {NETWORK} coverage.")
    if hard_fail:
        print(f"\nHARD FAILURES: {hard_fail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
