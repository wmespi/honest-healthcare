#!/usr/bin/env python3
"""
Coverage probe — "is our data complete for the primary use case?"

Runs a fixed basket of ~40 procedure codes against the backend and records, per
code, whether we have rates, how many provider groups / rates, the rate spread,
and how the target plan / network narrows it. Emits:

  - data/anthem/coverage_scorecard.json   (machine-readable, diffable)
  - a markdown table on stdout

Every code with has_rates == False is flagged GAP.

Usage:
  python3 scripts/coverage_probe.py [--api http://localhost:8000] [--label before]

Run it before and after a parse batch and diff the scorecards.
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request

# Primary use case: the user's mother's plan.
TARGET_PLAN_NAME = "BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM"
TARGET_NETWORK_NAME = "GA Blue Value HIX Individual Network"

# ~40 codes across the categories a real member would care about.
PROCEDURES = {
    "E/M — office / preventive": ["99213", "99214", "99204", "99396", "99385"],
    "Labs": ["80053", "85025", "80061", "83036", "84443", "87086"],
    "Imaging": ["71046", "70450", "72148", "73721", "76700", "77067"],
    "Procedures / surgery": ["45378", "43239", "29881", "27447", "66984", "47562", "49505", "63030"],
    "Obstetrics": ["59400", "59510"],
    "Cardiology": ["93000", "93306", "93452"],
    "Physical therapy": ["97110", "97140"],
    "Behavioral health": ["90837", "90834", "90791"],
    "Immunization / injection": ["96372", "90686"],
    "Emergency": ["99283", "99284"],
    "Anesthesia": ["00810"],
}


def get(api, path, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{api}{path}?{q}" if q else f"{api}{path}"
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def probe_code(api, code):
    row = {"billing_code": code}

    # Unfiltered distribution — do we have this code at all, network-wide?
    dist, err = get(api, "/rates/distribution", billing_code=code, billing_code_type="CPT")
    if err or not dist:
        row.update(has_rates=False, error=err or "no data")
        return row

    s = dist["summary"]
    row.update(
        has_rates=True,
        n_provider_groups=s["provider_groups"],
        n_rates=s["total_entries"],
        rate_min=s["min"],
        rate_median=s["median"],
        rate_max=s["max"],
    )

    # Provider-level detail (also surfaces settings / billing classes / networks).
    prov, _ = get(api, "/rates/providers", billing_code=code, billing_code_type="CPT", limit=1000)
    results = (prov or {}).get("results", [])
    row["n_rows_with_npi"] = sum(1 for r in results if r.get("npi_count", 0) > 0)
    row["networks_seen"] = sorted({r["network_name"] for r in results if r.get("network_name")})

    # Narrow to the target plan and target network — which (if either) has data?
    plan_dist, _ = get(api, "/rates/distribution", billing_code=code,
                       billing_code_type="CPT", plan_name=TARGET_PLAN_NAME)
    row["target_plan_has_rates"] = bool(plan_dist)
    row["target_plan_n_rates"] = (plan_dist or {}).get("summary", {}).get("total_entries", 0)

    net_dist, _ = get(api, "/rates/distribution", billing_code=code,
                      billing_code_type="CPT", network_name=TARGET_NETWORK_NAME)
    row["target_network_has_rates"] = bool(net_dist)
    row["target_network_n_rates"] = (net_dist or {}).get("summary", {}).get("total_entries", 0)

    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.getenv("API_URL", "http://localhost:8000"))
    ap.add_argument("--label", default="probe")
    ap.add_argument("--out", default="data/anthem/coverage_scorecard.json")
    args = ap.parse_args()

    health, err = get(args.api, "/")
    if err:
        print(f"backend unreachable at {args.api}: {err}", file=sys.stderr)
        sys.exit(1)

    networks, _ = get(args.api, "/networks")
    network_attribution_live = bool(networks) and isinstance(networks, list)
    if not network_attribution_live:
        print("NOTE: no network_name-attributed parquet yet — the target-network "
              "filter is inert (backend ignores it), so 'net✓' below is not "
              "meaningful until a post-attribution parse batch lands.\n", file=sys.stderr)

    scorecard = {
        "label": args.label,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "api": args.api,
        "backend_totals": {k: health.get(k) for k in ("total_rates", "total_providers")},
        "target_plan_name": TARGET_PLAN_NAME,
        "target_network_name": TARGET_NETWORK_NAME,
        "network_attribution_live": network_attribution_live,
        "categories": {},
    }

    gaps = []
    all_rows = []
    for category, codes in PROCEDURES.items():
        scorecard["categories"][category] = []
        for code in codes:
            row = probe_code(args.api, code)
            scorecard["categories"][category].append(row)
            all_rows.append((category, row))
            if not row.get("has_rates"):
                gaps.append(code)

    scorecard["summary"] = {
        "n_codes": len(all_rows),
        "n_with_rates": sum(1 for _, r in all_rows if r.get("has_rates")),
        "n_gaps": len(gaps),
        "gap_codes": gaps,
        "n_target_plan_covered": sum(1 for _, r in all_rows if r.get("target_plan_has_rates")),
        "n_target_network_covered": sum(1 for _, r in all_rows if r.get("target_network_has_rates")),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(scorecard, f, indent=2)

    # Markdown table
    print(f"\n## Coverage scorecard — `{args.label}`  ({scorecard['generated_at']})\n")
    print(f"- backend: {health.get('total_rates'):,} rates / {health.get('total_providers'):,} provider rows")
    su = scorecard["summary"]
    print(f"- codes with any rates: **{su['n_with_rates']}/{su['n_codes']}**  |  gaps: **{su['n_gaps']}** ({', '.join(su['gap_codes']) or 'none'})")
    print(f"- codes covered under target plan string: {su['n_target_plan_covered']}  |  under target network: {su['n_target_network_covered']}\n")
    print("| code | cat | rates? | prov grps | n rates | min | median | max | plan✓ | net✓ |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for category, r in all_rows:
        if r.get("has_rates"):
            print(f"| {r['billing_code']} | {category} | ✅ | {r['n_provider_groups']} | {r['n_rates']} | "
                  f"{r['rate_min']} | {r['rate_median']} | {r['rate_max']} | "
                  f"{'✅' if r.get('target_plan_has_rates') else '·'} | {'✅' if r.get('target_network_has_rates') else '·'} |")
        else:
            print(f"| {r['billing_code']} | {category} | ❌ GAP | — | — | — | — | — | — | — |")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
