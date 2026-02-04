import pandas as pd
import requests
import os
import sys
from urllib.parse import urlparse

# URL configuration (Consolidated from sources.json)
EMORY_URL = "https://www.emoryhealthcare.org/-/media/Project/EH/Emory/ui/pricing-transparency/csv/2025/oct/580566256_emory-university-hospital_standardcharges.csv"

def download_file(url, output_dir):
    """Downloads the file from the URL to the output directory."""
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        filename = os.path.basename(urlparse(url).path)
        filepath = os.path.join(output_dir, filename)
        
        print(f">>> [Downloader] Downloading {filename}...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, stream=True, timeout=60)
        response.raise_for_status()
        
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        print(f">>> [Downloader] Saved to {filepath}")
        return filepath
    except Exception as e:
        print(f"!!! [Downloader] Failed: {e}")
        return None

def preprocess_csv(filepath):
    """
    Detects and removes metadata rows from the top of CSVs.
    Attempts multiple encodings.
    """
    print(f">>> [Preprocessor] Preprocessing {filepath}...")
    
    encodings = ['utf-8', 'cp1252', 'latin1', 'iso-8859-1']
    lines = []
    used_encoding = None
    
    # 1. Read with correct encoding
    for encoding in encodings:
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                lines = f.readlines()
            used_encoding = encoding
            print(f">>> [Preprocessor] Read successfully with {encoding}")
            break
        except UnicodeDecodeError:
            continue
            
    if not lines:
        print(f"!!! [Preprocessor] Could not read {filepath} with supported encodings.")
        return False

    # Heuristic: Look for the 'header' line.
    header_keywords = ["billing_code", "code", "description", "price", "charge", "payer"]
    header_index = 0
    found_header = False
    
    for i, line in enumerate(lines[:20]):
        lower_line = line.lower()
        if lower_line.startswith("hospital_name"): # Metadata marker
            continue
            
        match_count = sum(1 for k in header_keywords if k in lower_line)
        if match_count >= 2: 
            header_index = i
            found_header = True
            print(f">>> [Preprocessor] Found header candidates at line {i+1}")
            break
    
    if found_header and header_index > 0:
        print(f">>> [Preprocessor] Removing {header_index} metadata rows.")
        clean_lines = lines[header_index:]
        # Write back as UTF-8
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(clean_lines)
        return True
    elif found_header and header_index == 0:
        print(">>> [Preprocessor] Header is at line 1. No stripping needed.")
        return True
    else:
        print("!!! [Preprocessor] No clear header found.")
        return False

def ingest_bronze_emory():
    print(">>> [Bronze] Starting Emory Pipeline (Pandas)")

    # 1. Define Paths
    # Use a temp directory for the raw download
    raw_dir = "/tmp/honest_healthcare_raw"
    os.makedirs(raw_dir, exist_ok=True)
    
    # Output directly to bronze
    bronze_output_dir = "/app/data/bronze"
    bronze_output_file = os.path.join(bronze_output_dir, "emory.parquet")
    
    os.makedirs(bronze_output_dir, exist_ok=True)
    
    # 2. Download (Transient)
    raw_path = download_file(EMORY_URL, raw_dir)
    if not raw_path:
        sys.exit(1)
        
    try:
        # 3. Preprocess
        preprocess_csv(raw_path)
        
        # 4. Ingest to Parquet
        print(f">>> [Bronze] Reading CSV for Parquet Conversion: {raw_path}")
        
        encodings = ['utf-8', 'cp1252', 'latin1']
        df = None
        
        for encoding in encodings:
            try:
                df = pd.read_csv(raw_path, encoding=encoding, on_bad_lines='skip', low_memory=False)
                break
            except UnicodeDecodeError:
                continue
                
        if df is None:
            print("!!! [Bronze] Error: Could not read file for ingestion.")
            sys.exit(1)
            
        print(f">>> [Bronze] Raw Schema (Columns): {df.columns.tolist()}")
        print(f">>> [Bronze] Shape: {df.shape}")

        print(f">>> [Bronze] Writing Parquet to {bronze_output_file}")
        df.to_parquet(bronze_output_file, index=False)
        print(">>> [Bronze] Done.")
        
    finally:
        # 5. Cleanup Raw Intermediate
        if os.path.exists(raw_path):
            print(f">>> [Cleanup] Removing transient raw file: {raw_path}")
            os.remove(raw_path)

if __name__ == "__main__":
    ingest_bronze_emory()
