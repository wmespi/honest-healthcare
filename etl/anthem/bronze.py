import json
import os
from etl.utils.streaming import stream_gzip_json
from etl.utils.logger import log

# Anthem Master Index URL (from ETL.md)
# Using the 2026-02-01 date from the archive/sample files as a likely current version
INDEX_URL = "https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/2026-02-01_anthem_index.json.gz"
OUTPUT_PATH = "data/anthem/index_urls.json"

def discover_anthem_urls(limit=None):
    """
    Scans the Anthem index and extracts medical rate file URLs.
    Deduplicates by URL and saves to data/anthem/index_urls.json.
    """
    log("🚀 Starting Anthem Bronze Layer: Index Discovery...")
    
    seen_urls = set()
    found_files = []
    count = 0
    
    try:
        # Anthem index structure: reporting_structure -> list of objects
        parser = stream_gzip_json(INDEX_URL, "reporting_structure.item")
        
        for item in parser:
            count += 1
            
            # Extract plan names for context
            plans = item.get("reporting_plans", [])
            plan_names = [p.get("plan_name", "Unknown") for p in plans]
            
            files = item.get("in_network_files", [])
            for f in files:
                url = f.get("location")
                desc = f.get("description", "").lower()
                
                if not url:
                    continue
                
                # Deduplication
                if url in seen_urls:
                    continue
                
                # Exclude non-medical
                if any(k in desc for k in ["vision", "dental", "pharmacy", "behavioral"]):
                    continue
                
                # Validate it's a rate file
                if "in-network-rates" in url or "negotiated-rates" in url:
                    found_files.append({
                        "description": f.get("description"),
                        "location": url,
                        "plan_names": plan_names
                    })
                    seen_urls.add(url)
            
            if count % 1000 == 0:
                log(f"  Scanned {count} reporting structures... Found {len(found_files)} unique URLs.")
            
            if limit and count >= limit:
                log(f"🛑 Limit of {limit} reached.")
                break
                
        # Save results
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, 'w') as f:
            json.dump(found_files, f, indent=2)
            
        log(f"✅ Discovery complete! Found {len(found_files)} unique rate file URLs.")
        log(f"📂 Saved to {OUTPUT_PATH}")
        
    except Exception as e:
        log(f"❌ Discovery failed: {e}")

if __name__ == "__main__":
    import sys
    # Get limit from command line args if present
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    discover_anthem_urls(limit=limit)
