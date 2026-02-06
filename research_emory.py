import pandas as pd
import numpy as np

bronze_path = "/app/data/bronze/emory_raw.csv"

def find_header_and_read(file_path):
    with open(file_path, 'r', encoding='latin-1') as f:
        for i, line in enumerate(f):
            if "description" in line.lower() and "standard_charge" in line.lower():
                return pd.read_csv(file_path, skiprows=i, encoding='latin-1')
    return pd.read_csv(file_path, encoding='latin-1')

print(">>> [Research] Reading Raw Bronze CSV for analysis...")
df = find_header_and_read(bronze_path)

print("\n>>> [Research] Available columns:")
print(df.columns)

code_cols = [c for c in df.columns if 'type' in c]

for col in code_cols:
    print(f"\n>>> Distribution for {col}:")
    print(df[col].value_counts().head(10))

# Check for rows where negotiated rates are NOT null
negotiated_cols = [c for c in df.columns if 'negotiated' in c or 'min' in c or 'max' in c]

print("\n>>> Non-null counts for negotiated rate columns:")
print(df[negotiated_cols].notnull().sum())

# Cross-reference: which code types have negotiated rates?
for col in code_cols:
    print(f"\n>>> [Analysis] Code type: {col}")
    populated_neg = df[df[negotiated_cols].notnull().any(axis=1)][col].value_counts()
    if not populated_neg.empty:
        print(f"Code types with SOME negotiated rate data:\n{populated_neg}")
    else:
        print("No negotiated rates found for this code column.")

# Check for Overlap between APC and HCPCS
print("\n>>> [Analysis] Overlap between APC and HCPCS:")
mask_apc = df['code|1|type'] == 'APC'
mask_hcpcs = df['code|3|type'] == 'HCPCS'
overlap = df[mask_apc & mask_hcpcs]
print(f"Count of rows with BOTH APC and HCPCS: {len(overlap)}")

if not overlap.empty:
    print("Sample of overlap rows:")
    print(overlap[['description', 'code|1', 'code|3'] + negotiated_cols].head(5))

# Check negotiated rates for rows that have HCPCS but NO APC
print("\n>>> [Analysis] HCPCS rows with NO APC:")
hcpcs_no_apc = df[mask_hcpcs & ~mask_apc]
hcpcs_with_rates = hcpcs_no_apc[hcpcs_no_apc[negotiated_cols].notnull().any(axis=1)]
print(f"HCPCS rows with NO APC and SOME rates: {len(hcpcs_with_rates)}")
