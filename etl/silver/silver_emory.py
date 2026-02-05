import pandas as pd
import os
import sys

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
    print(">>> [Silver] Starting Emory Processing (Raw -> Clean Mode)")

    # 1. Define Paths
    bronze_path = "/app/data/bronze/emory_raw.csv"
    silver_output_dir = "/app/data/silver"
    silver_output_file = os.path.join(silver_output_dir, "emory_cleaned.csv")
    
    # Ensure output dir exists
    os.makedirs(silver_output_dir, exist_ok=True)
    
    print(f">>> [Silver] Reading Raw Bronze CSV: {bronze_path}")
    
    try:
        # 2. Read Bronze (Dynamic Header Detection)
        df = find_header_and_read(bronze_path)
        
        print(">>> [Silver] Initial Data Shape:", df.shape)
        
        # 3. Clean / Transform (Silver Logic)
        
        # Normalize column names
        df.columns = [c.strip().lower() for c in df.columns]
        
        # Inject Facility Info (Hardcoded for this single-hospital pipeline)
        # We do this because the Bronze preprocessor stripped the metadata rows where this might have been.
        df["hospital_name"] = "Emory University Hospital"
        df["hospital_location"] = "1364 Clifton Rd NE, Atlanta, GA 30322" 
        df["hospital_address"] = "1364 Clifton Rd NE, Atlanta, GA 30322" 
        
        # Inject Effective Date (Derived from Source URL: .../2025/oct/...)
        df["effective_date"] = "2025-10-01"

        # FILTER: Outpatient Only (and 'both' which applies to outpatient)
        if "setting" in df.columns:
            print(">>> [Silver] Filtering for 'outpatient' and 'both' settings...")
            df = df[df["setting"].isin(["outpatient", "both"])]
            print(">>> [Silver] Post-Filter Shape:", df.shape)
            
        # COLUMN SELECTION & RENAMING
        # We define a strict map of { "source_col": "target_col" }
        column_map = {
            "hospital_name": "hospital_name",
            "hospital_location": "location",
            "hospital_address": "address",
            "effective_date": "effective_date",
            "code|1": "billing_code",
            "description": "description",
            "billing_class": "billing_class",
            "setting": "setting",
            "payer_name": "payer",
            "plan_name": "plan",
            "standard_charge|negotiated_dollar": "negotiated_rate",
            "standard_charge|gross": "gross_charge",
            "standard_charge|discounted_cash": "cash_price",
            "standard_charge|min": "min_negotiated_rate",
            "standard_charge|max": "max_negotiated_rate",
            "estimated_amount": "estimated_amount"
        }
        
        final_df = pd.DataFrame()
        
        found_cols = []
        for src, dst in column_map.items():
            if src in df.columns:
                final_df[dst] = df[src]
                found_cols.append(src)
            else:
                # Try fallback for 'code|1' vs 'code_1'
                alt_src = src.replace("|", "_")
                if alt_src in df.columns:
                    final_df[dst] = df[alt_src]
                    found_cols.append(alt_src)
        
        print(f">>> [Silver] Selected Columns: {found_cols}")
        
        # Basic cleanup on the clean data
        # Ensure description is string
        if "description" in final_df.columns:
            final_df["description"] = final_df["description"].fillna("").astype(str)
            
            # Extract Level and Procedure Type
            # Pattern: Look for "Level " followed by a number
            import re
            
            def parse_description(desc):
                # Default values
                level = "1"
                proc_type = desc
                
                # Regex to find "Level X"
                match = re.search(r"Level\s+(\d+)", desc, re.IGNORECASE)
                if match:
                    level = match.group(1)
                    # Remove "Level X" from description to get procedure type
                    # Also strip " - " or similar leading separators if left over
                    proc_type = re.sub(r"Level\s+\d+", "", desc, flags=re.IGNORECASE).strip()
                    proc_type = re.sub(r"^[\s\-\–]+", "", proc_type).strip() # Remove leading dashes
                
                return pd.Series([level, proc_type])

            final_df[["level", "procedure_type"]] = final_df["description"].apply(parse_description)
            
        # 4. Write Output
        print(f">>> [Silver] Writing CSV to {silver_output_file}")
        final_df.to_csv(silver_output_file, index=False)
        print(">>> [Silver] Done.")
        
    except Exception as e:
        print(f"!!! Error in Silver Processing: {e}")
        sys.exit(1)

if __name__ == "__main__":
    process_emory()
