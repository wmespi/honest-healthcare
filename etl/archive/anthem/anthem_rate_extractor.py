import requests
import ijson
import gzip
import json
import os
import sys
from etl.utils.logger import log

# Inputs
URL_FILE = "data/anthem/ga_anthem_urls.json"
NPI_FILE = "data/hospitals/hospitals.json"
# Output
OUTPUT_PATH = "data/anthem/ga_anthem_rates.json"
CHECKPOINT_PATH = "data/anthem/extraction_progress.json"

def load_checkpoint():
    if os.path.exists(CHECKPOINT_PATH):
        with open(CHECKPOINT_PATH, 'r') as f:
            return set(json.load(f))
    return set()

def save_checkpoint(completed_urls):
    os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
    with open(CHECKPOINT_PATH, 'w') as f:
        json.dump(list(completed_urls), f)

def extract_rates(limit=None):
    log("🚀 Anthem Georgia Rate Extraction started...")
    
    # 1. Load targets
    if not os.path.exists(NPI_FILE):
        log(f"❌ Error: {NPI_FILE} not found.")
        return
    with open(NPI_FILE, 'r') as f:
        hospitals = json.load(f)
        target_npis = {str(h['npi']) for h in hospitals}
    log(f"📍 Loaded {len(target_npis)} target NPIs.")

    # 2. Load URLs
    if not os.path.exists(URL_FILE):
        log(f"❌ Error: {URL_FILE} not found.")
        return
    with open(URL_FILE, 'r') as f:
        url_data = json.load(f)
        if limit:
            url_data = url_data[:limit]
    
    processed_urls = load_checkpoint()
    remaining = [u for u in url_data if u['location'] not in processed_urls]
    log(f"📡 Found {len(url_data)} total URLs. {len(processed_urls)} already processed.")

    all_results = []
    # If file exists, load it to append (simple persistence)
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, 'r') as f:
                all_results = json.load(f)
        except:
            all_results = []

    for i, meta in enumerate(remaining):
        url = meta['location']
        idx_str = f"[{i+1+len(processed_urls)}/{len(url_data)}]"
        filename = url.split('/')[-1].split('?')[0]
        
        log(f"\n📂 {idx_str} Processing: {filename}")
        
        try:
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()
            
            # Step 1: Fast scan for NPI presence
            log(f"  🔍 {idx_str} Pass 1: Searching for hospital NPIs...")
            d_stream = gzip.GzipFile(fileobj=response.raw)
            parser = ijson.items(d_stream, "provider_references.item")
            
            found_ref_ids = set()
            scan_count = 0
            for ref in parser:
                scan_count += 1
                group = ref.get("provider_groups", [])
                for g in group:
                    npi_list = g.get("npi", [])
                    if any(str(n) in target_npis for n in npi_list):
                        found_ref_ids.add(ref.get("provider_group_id"))
                
                if scan_count % 5000 == 0:
                    log(f"    - Scanned {scan_count} providers...")

            if not found_ref_ids:
                log(f"  ⚠️ {idx_str} No matching hospital NPIs found. Skipping.")
                processed_urls.add(url)
                save_checkpoint(processed_urls)
                continue

            log(f"  ✅ {idx_str} Found {len(found_ref_ids)} matching references. Extracting rates...")
            
            # Step 2: Extract rates for those IDs
            response = requests.get(url, stream=True, timeout=60)
            d_stream = gzip.GzipFile(fileobj=response.raw)
            rate_parser = ijson.items(d_stream, "in_network.item")
            
            file_rates = 0
            for record in rate_parser:
                # Check if this service relates to our providers
                # Some files use provider_references, some embed directly
                refs = record.get("provider_references", [])
                if any(r in found_ref_ids for r in refs):
                    # We found a pricing record for our hospitals!
                    all_results.append({
                        "file": filename,
                        "billing_code": record.get("billing_code"),
                        "name": record.get("name"),
                        "rates": record.get("negotiated_rates")
                    })
                    file_rates += 1

            log(f"  💰 {idx_str} Successfully mined {file_rates} rates from this file.")
            
            # Save incremental results and checkpoint
            with open(OUTPUT_PATH, 'w') as f:
                json.dump(all_results, f, indent=2)
            
            processed_urls.add(url)
            save_checkpoint(processed_urls)

        except Exception as e:
            log(f"  ❌ {idx_str} Error: {e}")

    log(f"\n✅ Extraction complete. Total rates in {OUTPUT_PATH}: {len(all_results)}")

if __name__ == "__main__":
    # Get limit from command line args if present
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    extract_rates(limit)
