import requests
import ijson
import gzip
import json
import os
import sys
from datetime import datetime

# Inputs
URL_FILE = "data/anthem/ga_anthem_urls.json"
NPI_FILE = "data/hospitals/hospitals.json"
# Output
OUTPUT_PATH = "data/anthem/ga_anthem_rates.json"
CHECKPOINT_PATH = "data/anthem/extraction_progress.json"

def log(msg):
    """Log to both current process stdout and main container stdout for Docker UI visibility"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg, flush=True)
    # Attempt to write to container's PID 1 stdout for Docker Log View visibility
    try:
        with open('/proc/1/fd/1', 'w') as f:
            f.write(f"{full_msg}\n")
    except Exception:
        pass

def load_checkpoint():
    if os.path.exists(CHECKPOINT_PATH):
        try:
            with open(CHECKPOINT_PATH, 'r') as f:
                return json.load(f)
        except:
            return {"processed_urls": []}
    return {"processed_urls": []}

def save_checkpoint(processed_urls):
    with open(CHECKPOINT_PATH, 'w') as f:
        json.dump({"processed_urls": list(processed_urls)}, f)

def save_results(results, metadata):
    with open(OUTPUT_PATH, 'w') as f:
        json.dump({
            "metadata": metadata,
            "results": results
        }, f, indent=2)

def extract_rates(limit_files=5):
    log(f"🚀 Anthem Georgia Rate Extraction started...")
    
    # 1. Load target NPIs
    with open(NPI_FILE, 'r') as f:
        hospitals = json.load(f)
    target_npis = {str(h['npi']) for h in hospitals}
    log(f"📍 Loaded {len(target_npis)} target NPIs.")

    # 2. Load URLs
    with open(URL_FILE, 'r') as f:
        url_data = json.load(f)
    urls = [item['location'] for item in url_data]
    
    checkpoint = load_checkpoint()
    processed_urls = set(checkpoint.get("processed_urls", []))
    
    # Initialize results
    final_results = []
    metadata = {
        "last_updated": datetime.now().isoformat(),
        "total_files_in_index": len(urls),
        "files_processed": len(processed_urls),
        "total_rates_found": 0
    }

    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, 'r') as f:
                existing = json.load(f)
                final_results = existing.get("results", [])
                metadata["total_rates_found"] = len(final_results)
        except:
            pass

    log(f"📡 Found {len(urls)} total URLs. {len(processed_urls)} already processed.")

    files_to_process = [u for u in urls if u not in processed_urls][:limit_files]
    
    if not files_to_process:
        log("✅ No new files to process in this batch.")
        return

    for i, url in enumerate(files_to_process):
        idx = len(processed_urls) + 1
        prog_str = f"[{idx}/{len(urls)}]"
        log(f"\n📂 {prog_str} Processing: {url.split('?')[0].split('/')[-1]}")
        
        try:
            # Pass 1: Build the ref_id_map
            log(f"  🔍 {prog_str} Pass 1: Searching for hospital NPIs...")
            
            res1 = requests.get(url, stream=True, timeout=60)
            res1.raise_for_status()
            gs1 = gzip.GzipFile(fileobj=res1.raw)
            ref_id_map = {}
            
            count = 0
            for ref in ijson.items(gs1, "provider_references.item"):
                count += 1
                ref_id = ref.get("provider_group_id")
                npi_list = []
                for prov in ref.get("provider_groups", []):
                    npi_list.extend(prov.get("npi", []))
                
                if any(str(npi) in target_npis for npi in npi_list):
                    ref_id_map[ref_id] = True
                
                if count % 10000 == 0:
                    log(f"    - Scanned {count} providers...")
            
            if not ref_id_map:
                log(f"  ⚠️ {prog_str} No matching hospital NPIs found. Skipping.")
                processed_urls.add(url)
                metadata["files_processed"] = len(processed_urls)
                metadata["last_updated"] = datetime.now().isoformat()
                save_results(final_results, metadata)
                save_checkpoint(processed_urls)
                continue
                
            log(f"  ✅ {prog_str} Matched {len(ref_id_map)} hospital groups. Extracting rates...")
            
            # Pass 2: Extract rates
            res2 = requests.get(url, stream=True, timeout=60)
            res2.raise_for_status()
            gs2 = gzip.GzipFile(fileobj=res2.raw)
            in_network_parser = ijson.items(gs2, "in_network.item")
            
            file_results_count = 0
            for item in in_network_parser:
                rates = item.get("negotiated_rates", [])
                matches_for_this_service = []
                
                for rate_group in rates:
                    prov_refs = rate_group.get("provider_references", [])
                    if any(ref in ref_id_map for ref in prov_refs):
                        for nr in rate_group.get("negotiated_prices", []):
                            matches_for_this_service.append({
                                "negotiated_type": nr.get("negotiated_type"),
                                "negotiated_rate": nr.get("negotiated_rate"),
                                "expiration_date": nr.get("expiration_date"),
                                "billing_class": nr.get("billing_class")
                            })
                
                if matches_for_this_service:
                    result = {
                        "billing_code": item.get("billing_code"),
                        "billing_code_type": item.get("billing_code_type"),
                        "description": item.get("name"),
                        "rates": matches_for_this_service,
                        "source_file": url.split('?')[0].split('/')[-1]
                    }
                    final_results.append(result)
                    file_results_count += 1

            processed_urls.add(url)
            metadata["files_processed"] = len(processed_urls)
            metadata["total_rates_found"] = len(final_results)
            metadata["last_updated"] = datetime.now().isoformat()
            
            log(f"  📈 {prog_str} Extracted {file_results_count} rates. Total: {len(final_results)}")
            
            # Save after EACH file
            save_results(final_results, metadata)
            save_checkpoint(processed_urls)
            log(f"  💾 {prog_str} Checkpoint saved.")

        except Exception as e:
            log(f"  ❌ {prog_str} Error: {e}")

    log(f"\n✅ Batch complete. {len(final_results)} entries total in {OUTPUT_PATH}")

if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    extract_rates(limit_files=limit)
