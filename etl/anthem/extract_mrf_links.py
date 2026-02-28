import json
import os
import requests
from concurrent.futures import ThreadPoolExecutor
from etl.utils.streaming import stream_gzip_json
from etl.utils.logger import log

# Anthem Master Index URL (from ETL.md)
INDEX_URL = "https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/2026-02-01_anthem_index.json.gz"
OUTPUT_PATH = "data/anthem/index_urls.json"

def get_file_size(url: str):
    """Helper to fetch Content-Length via HEAD request."""
    try:
        response = requests.head(url, timeout=5)
        return int(response.headers.get('Content-Length', 0))
    except:
        return 0

def discover_anthem_urls(limit=None):
    """
    Scans the Anthem index, extracts medical rate file URLs, and fetches their sizes.
    Deduplicates by URL and saves to data/anthem/index_urls.json.
    """
    log("🚀 Starting Anthem Bronze Layer: Index Discovery...")
    
    seen_urls = set()
    found_files = []
    count = 0
    
    try:
        parser = stream_gzip_json(INDEX_URL, "reporting_structure.item")
        
        # We collect all candidates first, then batch fetch sizes to keep it fast
        candidates = []
        
        for item in parser:
            count += 1
            
            plans = item.get("reporting_plans", [])
            plan_names = [p.get("plan_name", "Unknown") for p in plans]
            
            files = item.get("in_network_files", [])
            for f in files:
                url = f.get("location")
                desc = f.get("description", "").lower()
                
                if not url or url in seen_urls:
                    continue
                
                # Validate it's a rate file
                if "in-network-rates" in url or "negotiated-rates" in url:
                    candidates.append({
                        "description": f.get("description"),
                        "location": url,
                        "plan_names": plan_names
                    })
                    seen_urls.add(url)
            
            if count % 1000 == 0:
                log(f"  Scanned {count} reporting structures... Found {len(candidates)} candidates.")
            
            if limit and count >= limit:
                log(f"🛑 Limit of {limit} reached.")
                break

        log(f"📡 Fetching file sizes for {len(candidates)} URLs (Multi-threaded)...")
        # Fetch sizes in parallel
        with ThreadPoolExecutor(max_workers=20) as executor:
            urls_only = [c['location'] for c in candidates]
            sizes = list(executor.map(get_file_size, urls_only))
            
        for i, size in enumerate(sizes):
            candidates[i]["file_size_bytes"] = size

        # Save results
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, 'w') as f:
            json.dump(candidates, f, indent=2)
            
        log(f"✅ Discovery complete! Found {len(candidates)} unique rate file URLs.")
        log(f"📂 Saved to {OUTPUT_PATH}")
        
    except Exception as e:
        log(f"❌ Discovery failed: {e}")

if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    discover_anthem_urls(limit=limit)
