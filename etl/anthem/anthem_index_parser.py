import requests
import ijson
import gzip
import json
import os

INDEX_URL = "https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/2026-02-01_anthem_index.json.gz"
TARGET_ISSUERS = ["45334", "49046"]
OUTPUT_PATH = "data/anthem/ga_anthem_urls.json"

def find_ga_rate_files():
    print(f"📡 High-efficiency scan started for Anthem GA rates...")
    print(f"🎯 Targets: {', '.join(TARGET_ISSUERS)}")
    
    found_files = [] # List of {description, location, plans: []}
    seen_urls = set()
    
    try:
        response = requests.get(INDEX_URL, stream=True, timeout=60)
        response.raise_for_status()
        
        d_stream = gzip.GzipFile(fileobj=response.raw)
        
        # Stream reporting_structure items
        parser = ijson.items(d_stream, "reporting_structure.item")
        
        count = 0
        matches = 0
        
        for item in parser:
            count += 1
            plans = item.get("reporting_plans", [])
            
            # Check if this group covers our target GA issuers
            ga_plans = []
            for p in plans:
                p_id = p.get("plan_id", "")
                # Matching prefix
                if any(p_id.startswith(target) for target in TARGET_ISSUERS):
                    ga_plans.append({
                        "id": p_id,
                        "name": p.get("plan_name"),
                        "type": p.get("plan_id_type")
                    })
            
            if ga_plans:
                matches += 1
                files = item.get("in_network_files", [])
                
                # We only want MEDICAL in-network rates
                # Filtering for keywords that suggest medical data (or excluding non-medical)
                for f in files:
                    loc = f.get("location")
                    desc = f.get("description", "").lower()
                    
                    if loc and loc not in seen_urls:
                        # Specific exclusions for fragments we don't need (vision, dental, etc)
                        if any(k in desc for k in ["vision", "dental", "pharmacy", "behavioral"]):
                            continue
                            
                        # Only keep if it looks like a medical rate file
                        if "in-network-rates" in loc or "negotiated-rates" in loc or "medical" in desc:
                            found_files.append({
                                "description": f.get("description"),
                                "location": loc,
                                "plans": ga_plans
                            })
                            seen_urls.add(loc)
            
            if count % 10000 == 0:
                print(f"  Scanned {count} structures, matches: {matches}, unique URLs: {len(found_files)}", flush=True)
            
            # Safety break - the file is 11.7GB, but HIOS plans usually appear early or are clustered.
            # However, since this is a global index, we might need to scan deeper.
            # For now, let's limit to a reasonable discovery threshold.
            if len(found_files) >= 50:
                print("🛑 Found 50 unique GA URLs, stopping discovery.")
                break

        # Save findings
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, 'w') as out_f:
            json.dump(found_files, out_f, indent=2)
            
        print(f"\n✅ Discovery complete. Saved {len(found_files)} rate file targets to {OUTPUT_PATH}")

    except Exception as e:
        print(f"❌ Scan failed: {e}")

if __name__ == "__main__":
    find_ga_rate_files()
