import pandas as pd
import os
import sys
import json

def find_header_and_read(filepath):
    """
    Detects encoding and finds the header row index.
    Returns a cleaned DataFrame.
    """
    encodings = ['utf-8', 'cp1252', 'latin1', 'iso-8859-1']
    lines = []
    used_encoding = None
    
    # 1. Detect Encoding
    for encoding in encodings:
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                lines = f.readlines()
            used_encoding = encoding
            break
        except UnicodeDecodeError:
            continue
            
    if not lines:
        raise ValueError(f"Could not read {filepath} with supported encodings.")

    # 2. Find Header Row
    # We look for a row that has both 'description' and 'code|1' or 'payer_name'
    header_keywords = ["description", "code|1", "payer_name", "standard_charge"]
    header_index = 0
    found_header = False
    
    # Check first 50 rows for header
    for i, line in enumerate(lines[:50]):
        lower_line = line.lower()
        # Skip rows that are clearly metadata
        if lower_line.startswith("hospital_name") or lower_line.startswith("license_number"):
            continue
            
        match_count = sum(1 for k in header_keywords if k in lower_line)
        if match_count >= 2: 
            header_index = i
            found_header = True
            print(f">>> [Silver] Found header row at index {i} (line {i+1})")
            break
            
    if not found_header:
        print(">>> [Silver] Warning: No clear header found, assuming index 0.")

    # 3. Read CSV starting from header row
    df = pd.read_csv(filepath, encoding=used_encoding, skiprows=header_index, low_memory=False)
    
    # 4. Strip trailing empty/metadata rows
    # Often hospitals have footers. We'll drop rows where description or code is missing.
    # Emory often has a "To the best of its knowledge..." disclaimer at the top/bottom.
    initial_count = len(df)
    
    # We look for rows where 'description' is null or looks like a disclaimer
    if "description" in df.columns:
        df = df[df["description"].notnull()]
        df = df[~df["description"].str.contains("To the best of its knowledge", case=False, na=False)]
        
    if len(df) < initial_count:
        print(f">>> [Silver] Dropped {initial_count - len(df)} trailing/empty/disclaimer rows.")
            
    return df

def process_emory():
    print(">>> [Silver] Starting Emory Multi-Hospital Processing (Raw -> Clean Mode)")

    # 1. Define Paths
    bronze_dir = "/app/data/bronze"
    catalog_path = os.path.join(bronze_dir, "hospital_catalog.json")
    silver_output_dir = "/app/data/silver"
    silver_output_file = os.path.join(silver_output_dir, "emory_all_cleaned.csv")
    
    # Ensure output dir exists
    os.makedirs(silver_output_dir, exist_ok=True)
    
    # 1.5 Load Catalog
    if not os.path.exists(catalog_path):
        print(f"!!! [Silver] Hospital catalog not found: {catalog_path}. Run Bronze first.")
        return

    with open(catalog_path, 'r') as f:
        hospital_mapping = json.load(f)

    all_dfs = []

    # Get all raw files
    if not os.path.exists(bronze_dir):
        print(f"!!! [Silver] Bronze directory not found: {bronze_dir}")
        return

    raw_files = [f for f in os.listdir(bronze_dir) if f.endswith("_raw.csv")]
    
    try:
        for filename in sorted(raw_files):
            hospital_key = filename.replace("_raw.csv", "")
            hospital_name = hospital_mapping.get(hospital_key, hospital_key.replace("_", " ").title())
            bronze_path = os.path.join(bronze_dir, filename)
            
            print(f"\n>>> [Silver] Processing {hospital_name} ({filename})...")
            
            try:
                # 2. Read Bronze (Dynamic Header Detection)
                df = find_header_and_read(bronze_path)        
                print(f">>> [Silver] {hospital_name} Init Shape: {df.shape}")
                
                # Normalize column names
                df.columns = [c.strip().lower() for c in df.columns]
                
                # Inject Facility Information
                df["hospital_name"] = hospital_name
                df["hospital_address"] = "" # Leave empty for now
                df["effective_date"] = "2025-10-01"

                # COLUMN SELECTION & RENAMING
                column_map = {
                    "hospital_name": "hospital_name",
                    "hospital_address": "address",
                    "effective_date": "effective_date",
                    "code|1|type": "billing_code_type",
                    "code|1": "billing_code",
                    "description": "description",
                    "billing_class": "billing_class",
                    "setting": "setting",
                    "payer_name": "payer",
                    "plan_name": "plan",
                    "standard_charge|min": "min_negotiated_rate",
                    "standard_charge|max": "max_negotiated_rate",
                    "estimated_amount": "estimated_amount"
                }

                # Filter down to codes with inpatient/outpatient insurance information
                if 'code|1|type' in df.columns:
                    df = df[df['code|1|type'].isin(['MS-DRG', 'APC'])]
                elif 'code_1_type' in df.columns:
                    df = df[df['code_1_type'].isin(['MS-DRG', 'APC'])]

                cleaned_df = pd.DataFrame()
                for src, dst in column_map.items():
                    if src in df.columns:
                        cleaned_df[dst] = df[src]
                    else:
                        # Try fallback for 'code|1' vs 'code_1'
                        alt_src = src.replace("|", "_")
                        if alt_src in df.columns:
                            cleaned_df[dst] = df[alt_src]

                # Basic cleanup
                if not cleaned_df.empty and "description" in cleaned_df.columns:
                    cleaned_df["description"] = cleaned_df["description"].fillna("").astype(str)
                    
                    # Extract Level and Procedure Type
                    import re
                    def parse_description(desc):
                        level = None
                        proc_type = desc
                        match = re.search(r"Level\s+(\d+)", desc, re.IGNORECASE)
                        if match:
                            level = match.group(1)
                            # Remove "Level X" and any leading/trailing separators
                            clean_name = re.sub(r"Level\s+\d+", "", desc, flags=re.IGNORECASE).strip()
                            clean_name = re.sub(r"^[\s\-\–:|,]+|[\s\-\–:|,]+$", "", clean_name).strip()
                            proc_type = f"{clean_name} Level {level}"
                        
                        return pd.Series([level or "1", proc_type])

                    cleaned_df[["level", "procedure_type"]] = cleaned_df["description"].apply(parse_description)
                
                if not cleaned_df.empty:
                    all_dfs.append(cleaned_df)
                    print(f">>> [Silver] Finished {hospital_name}. Cleaned rows: {len(cleaned_df)}")
                else:
                    print(f">>> [Silver] Warning: {hospital_name} produced zero cleaned rows.")

            except Exception as e:
                print(f"!!! [Silver] Error processing {hospital_name}: {e}")

        # 4. Write Combined Output
        if all_dfs:
            final_df = pd.concat(all_dfs, ignore_index=True)
            print(f"\n>>> [Silver] Writing Combined CSV ({len(final_df)} rows) to {silver_output_file}")
            final_df.to_csv(silver_output_file, index=False)
            print(">>> [Silver] Done.")
        else:
            print("!!! [Silver] No data was processed.")
        
    except Exception as e:
        print(f"!!! Error in Silver Processing: {e}")
        sys.exit(1)

if __name__ == "__main__":
    process_emory()
