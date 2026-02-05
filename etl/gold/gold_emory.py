import pandas as pd
import os
import sys

def create_gold_layer():
    print(">>> [Gold] Starting Gold Layer Aggregation...")
    
    silver_path = "/app/data/silver/emory_cleaned.csv"
    gold_output_dir = "/app/data/gold"
    gold_output_file = os.path.join(gold_output_dir, "emory_summary.csv")
    
    os.makedirs(gold_output_dir, exist_ok=True)
    
    try:
        # 1. Read Silver Data
        df = pd.read_csv(silver_path, low_memory=False)
        print(f">>> [Gold] Read {len(df)} rows from {silver_path}")
        
        # 2. Logic: Create Global Lookup for Base Rates (Gross & Cash)
        # We calculate the max gross/cash seen ANYWHERE for a billing code.
        # This is more robust than looking only at empty-payer rows, as some codes might 
        # have the gross charge listed on a specific payer line but missing elsewhere.
        
        code_stats = df.groupby('billing_code')[['gross_charge', 'cash_price']].max().reset_index()
        
        # Rename for merge clarity
        code_stats = code_stats.rename(columns={
            'gross_charge': 'global_gross_charge', 
            'cash_price': 'global_cash_price'
        })
        
        # 3. Merge Global Rates back to Main Dataframe
        # Left join on billing_code
        merged_df = pd.merge(df, code_stats, on='billing_code', how='left')
        
        # 4. Filter for valid OUTPATIENT data (Procedure Type exists)
        valid_df = merged_df.dropna(subset=['procedure_type']).copy()
        
        # Fill Payer/Plan for Grouping
        valid_df[['payer', 'plan']] = valid_df[['payer', 'plan']].fillna("Not Specified")

        # 5. Aggregation
        summary = valid_df.groupby(['procedure_type', 'level', 'payer', 'plan'], dropna=False).agg(
            gross_charge=('global_gross_charge', 'max'),
            cash_price=('global_cash_price', 'max'),
            estimated_amount=('estimated_amount', 'max'),
            record_count=('billing_code', 'count')
        ).reset_index()
        
        # Sort
        summary['level'] = pd.to_numeric(summary['level'], errors='coerce').fillna(0)
        summary = summary.sort_values(by=['procedure_type', 'level', 'payer', 'plan'])
        
        # 5. Write Output
        print(f">>> [Gold] Writing {len(summary)} summary rows to {gold_output_file}")
        summary.to_csv(gold_output_file, index=False)
        print(">>> [Gold] Done.")
        
    except Exception as e:
        print(f"!!! Error in Gold Processing: {e}")
        sys.exit(1)

if __name__ == "__main__":
    create_gold_layer()
