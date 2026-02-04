import pandas as pd
import os
import sys

def process_emory():
    print(">>> [Silver] Starting Emory Processing (Pandas)")

    # 1. Define Paths
    bronze_path = "/app/data/bronze/emory.parquet"
    silver_output_dir = "/app/data/silver"
    silver_output_file = os.path.join(silver_output_dir, "emory_cleaned.csv")
    
    # Ensure output dir exists
    os.makedirs(silver_output_dir, exist_ok=True)
    
    print(f">>> [Silver] Reading Bronze Parquet: {bronze_path}")
    
    try:
        # 2. Read Bronze
        df = pd.read_parquet(bronze_path)
        
        print(">>> [Silver] Initial Columns:")
        print(df.columns.tolist())
        
        # 3. Clean / Transform (Silver Logic)
        
        # Normalize column names (strip whitespace, lowercase)
        df.columns = [c.strip().lower() for c in df.columns]
        
        # Renaming map based on previous inspection
        rename_map = {
            "code|1": "billing_code",
            "code_1": "billing_code", # If pandas normalized | to _
            "description": "procedure_description",
            # Add more as we discover them
        }
        
        df.rename(columns=rename_map, inplace=True)
        
        # Add standardized Hospital Name
        df["hospital_name"] = "Emory University Hospital"
        
        # Basic filtering (if we have billing_code)
        if "billing_code" in df.columns:
            # Filter for CPT codes starting with 9920 or 9921 (E&M) as an example
            # This logic mimics the previous Spark filter roughly
            # df = df[df["billing_code"].astype(str).str.contains(r"9920[2-5]|9921[1-5]", na=False)]
            pass

        # 4. Write Output
        print(f">>> [Silver] Writing CSV to {silver_output_file}")
        df.to_csv(silver_output_file, index=False)
        print(">>> [Silver] Done.")
        
    except Exception as e:
        print(f"!!! Error in Silver Processing: {e}")
        sys.exit(1)

if __name__ == "__main__":
    process_emory()
