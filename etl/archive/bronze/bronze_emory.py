import pandas as pd
import requests
import os
import sys
from urllib.parse import urlparse

import json

CMS_HPT_URL = "https://www.emoryhealthcare.org/cms-hpt.txt"

def discover_hospitals():
    """Fetches the CMS-HPT index and returns a list of hospitals with their URLs."""
    print(f">>> [Discovery] Fetching {CMS_HPT_URL}...")
    try:
        response = requests.get(CMS_HPT_URL, timeout=30)
        response.raise_for_status()
        content = response.text
        
        hospitals = []
        current_hospital = {}
        
        for line in content.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            
            if key == "location-name":
                if current_hospital.get("name"): # Previous hospital complete
                    hospitals.append(current_hospital)
                current_hospital = {"name": value}
            elif key == "mrf-url":
                current_hospital["url"] = value
        
        # Add final hospital
        if current_hospital.get("name"):
            hospitals.append(current_hospital)
            
        print(f">>> [Discovery] Found {len(hospitals)} hospitals.")
        return hospitals
    except Exception as e:
        print(f"!!! [Discovery] Failed: {e}")
        return []

import re

def download_file(url, output_dir, hospital_name):
    """Downloads the file from the URL to the output directory."""
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        # Standardize filename: lower, strip non-alphanumeric, collapse spaces/underscores
        clean_name = re.sub(r'[^a-z0-9]+', '_', hospital_name.lower()).strip('_')
        filename = f"{clean_name}_raw.csv"
        filepath = os.path.join(output_dir, filename)
        
        print(f">>> [Downloader] Downloading {hospital_name}...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, stream=True, timeout=60)
        response.raise_for_status()
        
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        print(f">>> [Downloader] Saved to {filepath}")
        return filepath, filename
    except Exception as e:
        print(f"!!! [Downloader] Failed for {hospital_name}: {e}")
        return None, None

def ingest_bronze_emory():
    print(">>> [Bronze] Starting Emory Dynamic Discovery Pipeline")

    # 1. Define Paths
    bronze_output_dir = "/app/data/bronze"
    catalog_path = os.path.join(bronze_output_dir, "hospital_catalog.json")
    os.makedirs(bronze_output_dir, exist_ok=True)
    
    # 2. Discover
    hospitals = discover_hospitals()
    if not hospitals:
        print("!!! [Bronze] No hospitals found. Exiting.")
        sys.exit(1)
        
    # 3. Save Catalog & Download
    catalog_mapping = {}
    success_count = 0
    total_count = len(hospitals)
    
    # Track current filenames to purge orphans later
    active_filenames = set()

    # 4. Ingest each hospital
    for hospital in hospitals:
        raw_path, raw_filename = download_file(hospital["url"], bronze_output_dir, hospital["name"])
        if raw_path:
            success_count += 1
            catalog_mapping[raw_filename] = hospital["name"]
            active_filenames.add(raw_filename)
            
    # 5. Cleanup Orphans
    # Delete any *_raw.csv files that are NOT in the current catalog
    print(">>> [Bronze] Syncing directory (Cleaning orphans)...")
    all_files = os.listdir(bronze_output_dir)
    for f in all_files:
        if f.endswith("_raw.csv") and f not in active_filenames:
            os.remove(os.path.join(bronze_output_dir, f))
            print(f">>> [Bronze] Deleted orphan file: {f}")

    # Save the filename -> official name mapping for Silver layer
    with open(catalog_path, 'w') as f:
        json.dump(catalog_mapping, f, indent=4)
        
    print(f">>> [Bronze] Done. Success: {success_count}/{total_count}")
    print(f">>> [Bronze] Catalog saved to {catalog_path}")

if __name__ == "__main__":
    ingest_bronze_emory()
