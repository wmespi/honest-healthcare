import pandas as pd
import os
import sys

def create_gold_layer():
    print(">>> [Gold] Starting Gold Layer Aggregation...")
    
    silver_path = "/app/data/silver/emory_cleaned.csv"
    gold_output_dir = "/app/data/gold"
    gold_output_file = os.path.join(gold_output_dir, "emory_gold.csv")
    
    os.makedirs(gold_output_dir, exist_ok=True)
    
    try:
        # 1. Read Silver Data
        df = pd.read_csv(silver_path, low_memory=False)
        print(f">>> [Gold] Read {len(df)} rows from {silver_path}")
        
        # 2. Fill Payer for Grouping
        df['payer'] = df['payer'].fillna("Self-Pay / Not Specified")
        df['plan'] = df['plan'].fillna("Standard")

        # 3. Aggregation
        # We group by Hospital, Code, and Payer/Plan to see the range for each insurance
        summary = df.groupby(['hospital_name', 'billing_code', 'procedure_type', 'level', 'payer', 'plan']).agg(
            min_rate=('min_negotiated_rate', 'min'),
            max_rate=('max_negotiated_rate', 'max'),
            median_rate=('estimated_amount', 'median'),
            record_count=('billing_code', 'count')
        ).reset_index()

        # 4. Filter out rows where we have absolutely no negotiated data
        initial_len = len(summary)
        summary = summary.dropna(subset=['min_rate', 'max_rate', 'median_rate'], how='all')
        print(f">>> [Gold] Filtered out {initial_len - len(summary)} summary rows with zero rate data.")

        # 5. Output
        print(f">>> [Gold] Writing {len(summary)} summary rows to {gold_output_file}")
        summary.to_csv(gold_output_file, index=False)
        print(">>> [Gold] Done.")
        
    except Exception as e:
        print(f"!!! Error in Gold Processing: {e}")
        sys.exit(1)

if __name__ == "__main__":
    create_gold_layer()
