import requests
import ijson
import gzip
import json
import os
from datetime import datetime

# Files
INDEX_URL = "https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/2026-02-01_anthem_index.json.gz"
ISSUER_FILE = "data/hospitals/ga_issuers.json"
OUTPUT_PATH = "data/anthem/ga_anthem_urls.json"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def find_ga_rate_files():
    log(f"🚀 Surgical Discovery: Starting Anthem Georgia Index Scan...")
    
    # 1. Load target Plan IDs from the Registry
    if not os.path.exists(ISSUER_FILE):
        log(f"❌ Error: {ISSUER_FILE} not found. Run update_ga_issuers.py first.")
        return

    with open(ISSUER_FILE, 'r') as f:
        issuers = json.load(f)
    
    # Collect all exact 14-digit Plan IDs for Anthem
    target_plans = set()
    target_issuers = set()
    for issuer in issuers:
        if "Anthem" in issuer["name"]:
            target_issuers.add(str(issuer["issuer_id"]))
            # Use full 14-digit StandardComponentId
            for pid in issuer.get("plan_ids", []):
                target_plans.add(pid)
    
    if not target_plans:
        log("❌ No Anthem plans found in registry.")
        return

    log(f"📍 Target Issuers: {target_issuers}")
    log(f"📍 Target Plans: {len(target_plans)} exact matches.")

    try:
        response = requests.get(INDEX_URL, stream=True, timeout=60)
        response.raise_for_status()
        
        d_stream = gzip.GzipFile(fileobj=response.raw)
        parser = ijson.items(d_stream, "reporting_structure.item")
        
        found_files = []
        seen_urls = set()
        count = 0
        matches = 0
        
        for item in parser:
            count += 1
            plans = item.get("reporting_plans", [])
            if not plans: continue

            plan_prefixes = [str(p.get("plan_id", ""))[:5] for p in plans]
            all_full_ids = [str(p.get("plan_id", "")) for p in plans]
            
            # 1. FAST FAIL: HIOS check
            # Only proceed to surgical match if this structure 
            # mentions one of our target 5-digit HIOS IDs
            if not any(pref in target_issuers for pref in plan_prefixes):
                continue

            # 3. SURGICAL MATCH: Exact 14-digit match
            matched_pids = [pid for pid in all_full_ids if pid in target_plans]
            
            if matched_pids:
                matches += 1
                files = item.get("in_network_files", [])
                
                for f in files:
                    loc = f.get("location")
                    desc = f.get("description", "").lower()
                    
                    if loc and loc not in seen_urls:
                        # Exclude non-medical
                        if any(k in desc for k in ["vision", "dental", "pharmacy", "behavioral"]):
                            continue
                            
                        # Basic validation
                        if "in-network-rates" in loc or "negotiated-rates" in loc:
                            found_files.append({
                                "description": f.get("description"),
                                "location": loc,
                                "matched_plans": matched_pids
                            })
                            seen_urls.add(loc)
            
            if count % 10000 == 0:
                current_prefs = sorted(list(set(plan_prefixes)))
                prefix_range = f"{min(current_prefs)} - {max(current_prefs)}" if current_prefs else "N/A"
                log(f"  Scanned {count} structures | HIOS Range: {prefix_range} | Matches: {matches}")

        # Save findings
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, 'w') as out_f:
            json.dump(found_files, out_f, indent=2)
            
        log(f"✅ Success! Saved {len(found_files)} targeted GA URLs to {OUTPUT_PATH}")

    except Exception as e:
        log(f"❌ Index scan failed: {e}")

if __name__ == "__main__":
    find_ga_rate_files()
