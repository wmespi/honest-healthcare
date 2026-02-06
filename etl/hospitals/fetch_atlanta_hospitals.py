import requests
import json
import os
import time

# NPPES API Base URL
BASE_URL = "https://npiregistry.cms.hhs.gov/api/"

# Seed NPIs for major facilities that are hard to find via name search
SEED_NPIS = [
    "1992799050", # Grady Memorial Hospital
    "1922178789", # CHOA Egleston
    "1730251695", # CHOA Scottish Rite
    "1881766749", # CHOA Arthur M. Blank
    "1679664395"  # Saint Joseph's Hospital
]

# Major Atlanta Hospital Systems to query (for discovering associated buildings/NPIs)
TARGET_SYSTEM_QUERIES = [
    "EMORY UNIVERSITY HOSPITAL",
    "PIEDMONT HOSPITAL",
    "NORTHSIDE HOSPITAL",
    "WELLSTAR ATLANTA MEDICAL CENTER",
    "EMORY UNIVERSITY HOSPITAL MIDTOWN"
]

# Strict Hospital Taxonomy Codes
HOSPITAL_TAXONOMIES = ["282N00000X", "282C00000X", "283Q00000X"]

def get_provider_by_npi(npi):
    """Fetches a single provider by NPI."""
    params = {"version": "2.1", "number": npi}
    try:
        response = requests.get(BASE_URL, params=params)
        if response.status_code == 200:
            results = response.json().get("results", [])
            return results[0] if results else None
    except Exception:
        return None
    return None

def fetch_deterministic_hospitals(output_file="data/hospitals/hospitals.json"):
    """
    Deterministically fetches core facilities using NPI seeds and system-targeted searches.
    """
    all_providers = []
    seen_npis = set()
    
    print(f"Targeting major Atlanta systems and Seed NPIs...")

    # Step 1: Process Seeds
    print("--- Processing Seed NPIs ---")
    for npi in SEED_NPIS:
        p = get_provider_by_npi(npi)
        if p:
            name = p.get("basic", {}).get("organization_name", "").upper()
            taxonomies = p.get("taxonomies", [])
            primary_tax = next((t for t in taxonomies if t.get("primary")), taxonomies[0])
            location = next((a for a in p.get("addresses", []) if a.get("address_purpose") == "LOCATION"), None)
            
            if location:
                all_providers.append({
                    "npi": npi,
                    "name": name,
                    "type": primary_tax.get("desc"),
                    "address": location.get("address_1"),
                    "city": location.get("city"),
                    "state": location.get("state"),
                    "zip": location.get("postal_code"),
                    "last_updated": p.get("basic", {}).get("last_updated")
                })
                seen_npis.add(npi)
                print(f"Verified Seed: {name} ({npi})")

    # Step 2: Process System Queries
    print("--- Querying Systems ---")
    for query in TARGET_SYSTEM_QUERIES:
        params = {
            "version": "2.1",
            "enumeration_type": "NPI-2",
            "organization_name": query,
            "city": "Atlanta",
            "state": "GA",
            "limit": 50
        }
        
        try:
            response = requests.get(BASE_URL, params=params)
            if response.status_code != 200:
                continue
            
            for p in response.json().get("results", []):
                npi = p.get("number")
                if npi in seen_npis:
                    continue

                name = p.get("basic", {}).get("organization_name", "").upper()
                taxonomies = p.get("taxonomies", [])
                primary_tax = next((t for t in taxonomies if t.get("primary")), taxonomies[0])
                
                if primary_tax.get("code") in HOSPITAL_TAXONOMIES:
                    location = next((a for a in p.get("addresses", []) if a.get("address_purpose") == "LOCATION"), None)
                    if location and location.get("city").upper() == "ATLANTA":
                        all_providers.append({
                            "npi": npi,
                            "name": name,
                            "type": primary_tax.get("desc"),
                            "address": location.get("address_1"),
                            "city": location.get("city"),
                            "state": location.get("state"),
                            "zip": location.get("postal_code"),
                            "last_updated": p.get("basic", {}).get("last_updated")
                        })
                        seen_npis.add(npi)
                        print(f"Verified System Building: {name} ({npi})")
            time.sleep(0.1)
        except Exception as e:
            print(f"Query failed for {query}: {e}")

    # Sort
    all_providers.sort(key=lambda x: x['name'])

    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(all_providers, f, indent=2)

    print(f"\nFinal Count: {len(all_providers)} verified Atlanta hospital buildings.")
    print(f"Saved to: {output_file}")

if __name__ == "__main__":
    fetch_deterministic_hospitals()
